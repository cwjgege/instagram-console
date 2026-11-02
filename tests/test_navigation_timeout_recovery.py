import json,sys,tempfile,unittest,time
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,WatchBudgetExpired,TransportLost,Halt
from page_state import PageState,PageBlocked
CFG=json.loads(Path('src/default-config.json').read_text())

def screen(*items):
 root=ET.Element('root')
 for tag,attrs in items:ET.SubElement(root,tag,{'visible':'true',**attrs})
 return root
MAIN=screen(('XCUIElementTypeButton',{'name':'reels-tab'}))
SEARCH=screen(('XCUIElementTypeSearchField',{'name':'search-text-input','value':''}),('XCUIElementTypeButton',{'name':'search-bar-back-button'}),('XCUIElementTypeOther',{'name':'search-bar-with-cancel'}))
REEL=screen(('XCUIElementTypeButton',{'name':'reels-tab','value':'1'}),('XCUIElementTypeButton',{'name':'like-button'}),('XCUIElementTypeButton',{'name':'comment-button'}))
class NavigationTimeoutRecovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.events=[]
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name,'device_udid':'b'*20},notify=self.events.append)
  self.r.wait=Mock();self.r.gesture=Mock();self.r.recover_connection=Mock()
 def test_known_nonvideo_page_does_not_exhaust_watch_budget_or_swipe(self):
  r=self.r
  for tree in (MAIN,SEARCH):
   r.snapshot=Mock(side_effect=[tree,WatchBudgetExpired()]);r.watch_read_deadline=time.monotonic()+8
   with self.assertRaisesRegex(NavigationLost,'已离开视频页'):r.read_reel()
   self.assertEqual(r.snapshot.call_count,1);r.gesture.assert_not_called()
 def test_watch_timeout_after_observed_navigation_does_not_request_swipe(self):
  r=self.r;r.rng=Mock();r.rng.random.return_value=0
  def read():r.page_state=PageState('main','已识别主导航');raise WatchBudgetExpired()
  r.read_reel=Mock(side_effect=read)
  with self.assertRaisesRegex(NavigationLost,'已离开视频页'):r.watch_video({'key':'old','progress':50},True)
  self.assertIsNone(r.watch_read_deadline);r.gesture.assert_not_called();r.recover_connection.assert_not_called()
  self.assertFalse(any(e['event']=='watch_progress_skipped' for e in self.events))
 def test_stale_video_cannot_authorize_fallback_swipe_on_search_page(self):
  r=self.r;r.page_state=PageState('search_input','已识别顶部搜索输入区')
  with self.assertRaises(NavigationLost):r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_not_called();self.assertEqual(r.swipes,0)
 def test_acknowledged_swipe_page_change_goes_to_navigation_recovery(self):
  r=self.r;r.snapshot=Mock(return_value=MAIN)
  with self.assertRaises(NavigationLost):r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();self.assertEqual(r.swipes,1);r.recover_connection.assert_not_called()
 def test_nonvideo_readback_after_transport_wait_goes_to_navigation_recovery(self):
  r=self.r;r.read_reel=Mock(side_effect=TransportLost('slow'))
  def response(**kwargs):r.page_state=PageState('main','主导航');return MAIN
  r.wait_for_page_response=Mock(side_effect=response)
  with self.assertRaises(NavigationLost):r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();r.wait_for_page_response.assert_called_once();r.recover_connection.assert_not_called()
 def test_uncertain_swipe_page_change_stops_without_navigation_writes(self):
  r=self.r;r.snapshot=Mock(return_value=MAIN)
  def gesture(*args,**kwargs):r._gesture_action_attempted=True;raise TransportLost('timed out')
  r.gesture.side_effect=gesture;r.click=Mock()
  with self.assertRaisesRegex(RuntimeError,'滑动响应无法确认'):r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();r.click.assert_not_called();r.recover_connection.assert_not_called()
 def test_alert_is_never_swallowed_as_navigation_recovery(self):
  self.r.snapshot=Mock(return_value=screen(('XCUIElementTypeAlert',{})))
  self.r.watch_read_deadline=time.monotonic()+8
  with self.assertRaises(PageBlocked):self.r.read_reel()
 def test_actual_recovery_navigates_main_back_to_reel_without_reconnect(self):
  r=self.r;r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});r.snapshot=Mock(return_value=MAIN)
  r.read_reel=Mock(side_effect=[NavigationLost('main'),{'key':'new'}]);r.click=Mock()
  self.assertEqual(r.recover_reels()['key'],'new')
  r.click.assert_called_once_with('reels-tab');r.gesture.assert_not_called();r.recover_connection.assert_not_called()
 def test_unknown_composer_is_preserved(self):
  r=self.r;r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'})
  r.snapshot=Mock(return_value=screen(('XCUIElementTypeTextView',{'value':'draft'}),('XCUIElementTypeButton',{'name':'reels-tab'})))
  r.read_reel=Mock(side_effect=NavigationLost('input'));r.click=Mock()
  with self.assertRaisesRegex(RuntimeError,'保留当前输入'):r.recover_reels()
  r.click.assert_not_called();r.gesture.assert_not_called()
 def test_main_loop_recovers_and_continues_skipping_interaction_on_recovered_video(self):
  r=self.r;r.connect=Mock();r.reels=Mock();r.diagnose=Mock();r.consider_like=Mock();r.search_optional=Mock()
  post={'key':'new','author':'test','caption':'','nodes':[]}
  r.read_reel=Mock(side_effect=[NavigationLost('已离开视频页'),post])
  def recover():self.assertTrue(r.skip_interactions_once);self.assertIsNone(r.video_watch);return post
  r.recover_reels=Mock(side_effect=recover)
  def watch(*args):r.stop.set();return None
  r.watch_video=Mock(side_effect=watch)
  with patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(Path(self.tmp.name)/'locks')):self.assertEqual(r.run(),'stopped')
  r.recover_reels.assert_called_once();r.watch_video.assert_called_once();r.consider_like.assert_not_called();r.search_optional.assert_not_called();r.recover_connection.assert_not_called()

if __name__=='__main__':unittest.main()
