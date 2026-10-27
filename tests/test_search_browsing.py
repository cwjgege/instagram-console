import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,ELEMENT,validate_motion,SearchTimedOut,Halt,search_budget_seconds
CFG=json.loads(Path('src/default-config.json').read_text())
def results(term='AI video creation',video=True):
 root=ET.Element('root')
 for tag,attrs in [('XCUIElementTypeSearchField',dict(name='search-text-input',value=term)),
   ('XCUIElementTypeButton',dict(name='search-for-you-serp',value='1'))]+([
   ('XCUIElementTypeCell',dict(name='media-discovery-cell',label='Video by a',y='143',height='276')),
   ('XCUIElementTypeCell',dict(name='media-discovery-cell',label='Video by b',y='419',height='276'))] if video else []):
  ET.SubElement(root,tag,dict(visible='true',**attrs))
 return root
class SearchBrowsing(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.events=[]
  self.r=Runner({**CFG,'runtime_dir':str(Path(self.tmp.name).resolve())},notify=self.events.append)
  self.r.wait=Mock();self.r.click=Mock();self.r.find=Mock(return_value='query')
  self.r.command=Mock(return_value=[{ELEMENT:'result'}]);self.r.gesture=Mock();self.r.reels=Mock()
  self.r.snapshot=Mock(return_value=results())
  node=ET.Element('XCUIElementTypeButton',dict(name='back-button'))
  self.r.read_reel=Mock(return_value=dict(key='post',author='a',nodes=[node]))
 def tearDown(self):self.tmp.cleanup()
 def test_opens_watches_returns_and_scrolls(self):
  self.r.search('AI video creation')
  self.assertEqual(self.r.search_videos,2);self.assertEqual(self.r.counts['watched'],2)
  self.assertEqual(self.r.click.call_args_list.count(unittest.mock.call('back-button')),2)
  self.r.gesture.assert_called_once_with('up');self.r.reels.assert_called_once()
  self.assertEqual([e['event'] for e in self.events if e['event'] not in ('page_state','search_stage','search_budget')],['search','search_watch','search_scroll','search_watch','search_return_complete'])
  self.assertEqual(self.r.counts['likes']+self.r.counts['saves']+self.r.counts['comments'],0)
 def test_empty_results_do_not_tap_arbitrary_locations(self):
  self.r.snapshot.return_value=results(video=False);self.r.search('AI video creation')
  self.r.read_reel.assert_not_called();self.r.gesture.assert_not_called();self.r.reels.assert_called_once()
 def test_wrong_query_is_not_a_valid_result_page(self):
  self.r.snapshot.return_value=results('unrelated')
  with self.assertRaises(NavigationLost):self.r.search_results('AI video creation')
 def test_keyboard_blocks_grid_actions(self):
  tree=results();ET.SubElement(tree,'XCUIElementTypeKeyboard',dict(visible='true'));self.r.snapshot.return_value=tree
  with self.assertRaises(NavigationLost):self.r.search_results('AI video creation')
 def test_unknown_overlay_blocks_grid_actions(self):
  tree=results();ET.SubElement(tree,'XCUIElementTypeOther',dict(visible='true',name='ig-partial-modal-sheet-view-controller-content'));self.r.snapshot.return_value=tree
  with self.assertRaises(NavigationLost):self.r.search_results('AI video creation')
 def test_missing_video_back_control_stops_search(self):
  self.r.read_reel.return_value=dict(key='a',author='a',nodes=[])
  with self.assertRaises(NavigationLost):self.r.search('AI video creation')
  self.r.gesture.assert_not_called()
 def test_non_english_query_never_enters_search(self):
  self.r.search('AI视频赚钱');self.r.click.assert_not_called()
 def test_search_limits_validate_before_device_operation(self):
  for change in [dict(search_videos_per_query=99),dict(search_result_scrolls=0),dict(search_watch_min=30,search_watch_max=10)]:
   with self.assertRaises(ValueError):validate_motion({**self.r.cfg,**change})
if __name__=='__main__':unittest.main()

class SearchBudget(unittest.TestCase):
 def test_expired_search_budget_cleared_before_return_navigation(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   r=Runner({**CFG,'runtime_dir':directory},notify=lambda _:None);r.diagnose=Mock()
   def stuck(term):
    r.search_deadline=time.monotonic()-1;r.check()
   r.search=stuck
   r.return_from_search=Mock(side_effect=lambda:self.assertIsNone(r.search_deadline))
   self.assertFalse(r.search_optional('AI video creation'));self.assertIsNone(r.search_deadline)
   self.assertFalse(r.feature_state['search']['enabled']);r.return_from_search.assert_called_once()
 def test_search_request_timeout_uses_remaining_budget(self):
  import time
  with tempfile.TemporaryDirectory() as directory:
   r=Runner({**CFG,'runtime_dir':directory});r.path='/session/test';r.http=Mock(return_value={});r.search_deadline=time.monotonic()+2
   r.command('GET','/source')
   self.assertGreater(r.http.call_args.kwargs['timeout'],0);self.assertLessEqual(r.http.call_args.kwargs['timeout'],2)


class SearchStageBudget(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=1000.;self.events=[]
  self.clock=patch('runner.time.monotonic',side_effect=lambda:self.now);self.clock.start()
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name},notify=self.events.append)
  self.r.feature_state['search']['enabled']=True;self.r.diagnose=Mock();self.r.return_from_search=Mock()
 def tearDown(self):self.clock.stop();self.tmp.cleanup()
 def advance(self,seconds,value=None):
  self.now+=seconds;self.r.check();return value
 def test_slow_healthy_two_video_search_outlives_old_130_second_cap(self):
  r=self.r;node=ET.Element('XCUIElementTypeButton',dict(name='back-button'))
  r.click=lambda *a:self.advance(7)
  r.search_input=lambda:self.advance(10,'query')
  r.command=lambda *a:self.advance(6,[{ELEMENT:'result'}])
  r.snapshot=lambda:self.advance(6,results())
  r.read_reel=lambda:self.advance(8,dict(key='post',author='a',nodes=[node]))
  r.gesture=lambda *a:self.advance(5);r.reels=lambda **kw:self.advance(5)
  r.wait=lambda seconds:self.advance(seconds);r.rng.uniform=lambda a,b:b
  start=self.now
  self.assertTrue(r.search_optional('AI video creation'))
  self.assertGreater(self.now-start,130);self.assertLess(self.now-start,370)
  self.assertEqual(r.search_videos,2);self.assertTrue(r.feature_state['search']['enabled'])
  self.assertIsNone(r.search_deadline);self.assertIsNone(r.search_overall_deadline)
  r.return_from_search.assert_not_called()
 def test_stalled_phase_expires_and_clears_both_clocks_before_recovery(self):
  r=self.r
  def stuck(term):r.search_stage('locate_input');self.advance(46)
  r.search=stuck
  def recover():self.assertIsNone(r.search_deadline);self.assertIsNone(r.search_overall_deadline)
  r.return_from_search.side_effect=recover
  self.assertFalse(r.search_optional('AI video creation'))
  self.assertIn('查找搜索框',r.feature_state['search']['reason']);r.return_from_search.assert_called_once()
 def test_phase_progress_cannot_extend_overall_limit(self):
  r=self.r;r.search_overall_deadline=self.now+100
  for _ in range(3):r.search_stage('select_result');self.advance(30)
  r.search_stage('open_video');self.assertEqual(r.search_deadline,r.search_overall_deadline)
  with self.assertRaisesRegex(SearchTimedOut,'整体时间'):self.advance(11)
 def test_watching_and_return_have_independent_budgets(self):
  r=self.r;r.search_stage('watch_result',20)
  self.assertEqual(r.search_deadline,self.now+30);self.advance(20)
  r.search_stage('return_results');self.assertEqual(r.search_deadline,self.now+45)
 def test_budget_scales_with_settings_and_is_capped(self):
  self.assertEqual(search_budget_seconds({**CFG,'search_videos_per_query':2,'search_watch_max':20,'search_result_scrolls':1}),370)
  self.assertEqual(search_budget_seconds({**CFG,'search_videos_per_query':5,'search_watch_max':90,'search_result_scrolls':3}),900)
 def test_http_uses_tighter_overall_limit(self):
  r=self.r;r.path='/session/test';r.http=Mock(return_value={})
  r.search_deadline=self.now+45;r.search_overall_deadline=self.now+2;r.command('GET','/source')
  self.assertEqual(r.http.call_args.kwargs['timeout'],2)
 def test_run_duration_and_manual_stop_remain_authoritative(self):
  r=self.r;r.deadline=self.now-1;r.search_overall_deadline=self.now-1
  with self.assertRaisesRegex(Halt,'时长'):r.check()
  r.stop.set()
  with self.assertRaisesRegex(Halt,'手动停止'):r.check()
 def test_cancellation_clears_search_clocks_without_recovery(self):
  r=self.r
  def cancel(term):r.search_stage('locate_input');raise Halt('已手动停止')
  r.search=cancel
  with self.assertRaises(Halt):r.search_optional('AI video creation')
  self.assertIsNone(r.search_deadline);self.assertIsNone(r.search_overall_deadline)
  r.return_from_search.assert_not_called()

 def test_timed_out_write_is_not_replayed_or_mislabeled_as_disconnect(self):
  r=self.r;r.path='/session/test';r.search_stage('submit_query')
  def timeout(*args,**kwargs):self.now+=45;raise TimeoutError('timed out')
  r.http=Mock(side_effect=timeout)
  with self.assertRaisesRegex(SearchTimedOut,'提交搜索词'):r.command('POST','/element/query/value',{'text':'AI'})
  self.assertEqual(r.http.call_count,1)
