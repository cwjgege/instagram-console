import json,sys,tempfile,time,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path('src').resolve()))
from page_state import collection_sheet_close,collection_sheet_nodes,PageBlocked
from runner import Runner,InteractionSkipped,NavigationLost,ControlHTTPError
CFG=json.loads(Path('src/default-config.json').read_text())
def sheet(title='Save to collection',close='Close',parent_visible='true'):
 root=ET.Element('root');modal=ET.SubElement(root,'XCUIElementTypeOther',name='ig-partial-modal-sheet-view-controller-content',visible=parent_visible)
 ET.SubElement(modal,'XCUIElementTypeStaticText',label=title,visible='true')
 ET.SubElement(modal,'XCUIElementTypeButton',name='close-button',label=close,visible='true',enabled='true')
 return root
class SaveSheet(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.r=Runner({**CFG,'runtime_dir':self.tmp.name},notify=lambda _:None);self.r.wait=Mock();self.r.command=Mock(return_value=[{'ELEMENT':'close'}])
 def tearDown(self):self.tmp.cleanup()
 def test_only_collection_modal_with_unique_close_is_eligible(self):
  for title,label in [('Save to collection','Close'),('Add to a collection','Close'),('Choose a collection','Cancel'),('选择收藏夹','取消'),('收藏夹','完成')]:
   self.assertIsNotNone(collection_sheet_close(sheet(title,label)))
  self.assertIsNone(collection_sheet_close(sheet('Unrelated menu')))
 def test_hidden_modal_container_with_visible_children_is_recognized(self):self.assertIsNotNone(collection_sheet_close(sheet(parent_visible='false')))
 def test_never_closes_input_keyboard_or_account_alert(self):
  for tag in ('XCUIElementTypeTextField','XCUIElementTypeTextView','XCUIElementTypeKeyboard','XCUIElementTypeAlert'):
   tree=sheet();ET.SubElement(tree,tag,visible='true',value='keep this');self.assertIsNone(collection_sheet_close(tree))
 def test_caption_or_plain_saved_text_does_not_identify_a_sheet(self):
  tree=ET.Element('root');ET.SubElement(tree,'XCUIElementTypeStaticText',label='Saved',visible='true');self.assertFalse(collection_sheet_nodes(tree))
 def test_duplicate_close_buttons_are_not_clicked(self):
  tree=sheet();ET.SubElement(tree[0],'XCUIElementTypeButton',name='cancel',label='Cancel',visible='true');self.assertFalse(self.r.close_collection_sheet(tree));self.r.command.assert_not_called()
 def test_closes_only_unique_observed_collection_control(self):
  self.assertTrue(self.r.close_collection_sheet(sheet()));self.assertEqual(self.r.command.call_args.args,('POST','/element/close/click',{}));self.assertEqual(self.r.command.call_count,2)
 def test_security_prompt_is_not_automatically_closed(self):
  tree=sheet();ET.SubElement(tree,'XCUIElementTypeStaticText',visible='true',label='Confirm your identity')
  with self.assertRaises(PageBlocked):self.r.close_collection_sheet(tree)
  self.r.command.assert_not_called()
 def test_recovery_closes_persistent_collection_sheet_then_confirms_reels(self):
  self.r.read_reel=Mock(side_effect=[NavigationLost('sheet'),{'key':'post'}]);self.r.snapshot=Mock(return_value=sheet());self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.click=Mock()
  self.assertEqual(self.r.recover_reels()['key'],'post');self.r.click.assert_not_called();self.assertEqual(self.r.command.call_args.args[1],'/element/close/click')
 def test_expired_save_budget_never_sends_another_write(self):
  self.r.command=Runner.command.__get__(self.r);self.r.path='/session/test';self.r.http=Mock();self.r.interaction_deadline=time.monotonic()-1
  with self.assertRaises(InteractionSkipped):self.r.command('POST','/element/save/click',{})
  self.r.http.assert_not_called()
 def test_save_request_is_bounded_by_remaining_confirmation_budget(self):
  self.r.command=Runner.command.__get__(self.r);self.r.path='/session/test';self.r.http=Mock(return_value={});self.r.interaction_deadline=time.monotonic()+2
  self.r.command('GET','/source');self.assertLessEqual(self.r.http.call_args.kwargs['timeout'],2)
 def test_changed_video_after_uncertain_save_does_not_skip_an_extra_video(self):
  self.r.read_reel=Mock(return_value={'key':'new'});self.r.swipe=Mock();self.r.continue_after_interaction({'key':'old'});self.r.swipe.assert_not_called();self.assertFalse(self.r.skip_interactions_once)
 def test_same_video_is_skipped_immediately_without_another_dwell(self):
  self.r.read_reel=Mock(side_effect=[{'key':'old'},{'key':'new'}]);self.r.swipe=Mock();self.r.continue_after_interaction({'key':'old'});self.r.swipe.assert_called_once_with('up');self.r.wait.assert_not_called();self.assertFalse(self.r.skip_interactions_once)
 def test_untouched_collection_search_can_be_closed(self):
  tree=sheet();ET.SubElement(tree[0],'XCUIElementTypeSearchField',visible='true',value='Search collections',placeholderValue='Search collections')
  self.assertIsNotNone(collection_sheet_close(tree))
 def test_typed_collection_search_is_preserved(self):
  tree=sheet();ET.SubElement(tree[0],'XCUIElementTypeSearchField',visible='true',value='my travel',placeholderValue='Search collections')
  self.assertIsNone(collection_sheet_close(tree));self.assertFalse(self.r.close_collection_sheet(tree));self.r.command.assert_not_called()
