import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,ELEMENT,validate_motion
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
  self.assertEqual([e['event'] for e in self.events if e['event']!='page_state'],['search','search_watch','search_scroll','search_watch'])
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
