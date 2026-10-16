import json,sys,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,search_input_node,search_layout,search_back_name,toggle_is_active
CFG=json.loads(Path('src/default-config.json').read_text())
def node(tag='XCUIElementTypeButton',**attrs):return ET.Element(tag,dict(visible='true',**attrs))
def tree(*nodes):
 r=ET.Element('XCUIElementTypeApplication',height='896');r.extend(nodes);return r
def meta(term='AI video creation',draft=''):
 return tree(node(name=term,label=term,y='62',height='21'),node(name='For you',label='For you',value='1'),
  node('XCUIElementTypeOther',name='search-bar-with-cancel',y='806'),node('XCUIElementTypeTextView',name='search-bar-text-view',y='813',height='32',value=draft),
  node(name='search-bar-new-journey-button'),node(name='Back'),node('XCUIElementTypeCell',name='media-discovery-cell',label='Video by demo',y='214',height='259'))
class Variants(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.r=Runner({**CFG,'runtime_dir':str(Path(self.tmp.name).resolve())},notify=Mock());self.r.wait=Mock();self.r.click=Mock()
 def tearDown(self):self.tmp.cleanup()
 def test_meta_focused_field_changes_type_and_identifier(self):
  t=tree(node('XCUIElementTypeOther',name='search-bar-text-view',y='56',height='33'),node('XCUIElementTypeTextView',name='search-bar-text-view',y='57',height='32'))
  self.assertEqual(search_input_node(t).tag,'XCUIElementTypeTextView')
 def test_classic_input_still_supported(self):
  self.assertEqual(search_input_node(tree(node('XCUIElementTypeSearchField',name='search-text-input',y='57',height='36'))).get('name'),'search-text-input')
 def test_bottom_followup_never_used_for_search_keywords(self):self.assertIsNone(search_input_node(meta()))
 def test_duplicate_search_inputs_are_rejected(self):
  t=tree(*[node('XCUIElementTypeSearchField',name='search-text-input',y='57',height='36') for _ in range(2)])
  self.assertIsNone(search_input_node(t))
 def test_meta_results_do_not_require_legacy_field(self):self.assertEqual(search_layout(meta(),'AI video creation'),'meta-search')
 def test_scrolled_selected_tab_can_be_hidden_by_fixed_header(self):
  t=meta();next(e for e in t.iter() if e.get('name')=='For you').set('visible','false')
  self.assertEqual(search_layout(t,'AI video creation'),'meta-search')
 def test_selected_profiles_blocks_stale_hidden_for_you_state(self):
  t=meta();t.append(node(name='Profiles',label='Profiles',value='1'))
  self.assertIsNone(search_layout(t,'AI video creation'))
 def test_meta_wrong_query_draft_and_keyboard_are_rejected(self):
  self.assertIsNone(search_layout(meta('wrong'),'AI video creation'));self.assertIsNone(search_layout(meta(draft='my draft'),'AI video creation'))
  t=meta();t.append(node('XCUIElementTypeKeyboard'));self.assertIsNone(search_layout(t,'AI video creation'))
 def test_missing_search_input_is_recoverable(self):
  self.r.snapshot=Mock(return_value=tree())
  with self.assertRaises(NavigationLost):self.r.search_input()
  self.r.click.assert_not_called()
 def test_meta_recovery_preserves_user_draft(self):
  self.r.snapshot=Mock(return_value=meta(draft='my draft'));self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.read_reel=Mock(side_effect=NavigationLost('not reels'))
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def test_meta_results_recover_through_observed_back(self):
  self.r.snapshot=Mock(return_value=meta());self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.read_reel=Mock(side_effect=[NavigationLost('not reels'),{'key':'new'}])
  self.r.recover_reels();self.r.click.assert_called_once_with('Back')
 def test_failed_search_is_cooled_down_before_retry(self):
  clock=[0.0];counter=[0];self.r.seconds=120;self.r.cfg.update(device_udid='A'*20,max_swipes_per_run=20,enable_comments=False)
  self.r.wait=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
  self.r.connect=Mock();self.r.reels=Mock();self.r.recover_reels=Mock()
  def post():
   counter[0]+=1;return {'key':str(counter[0]),'author':'demo','caption':'unrelated','nodes':[]}
  self.r.read_reel=post;self.r.swipe=lambda direction:setattr(self.r,'swipes',self.r.swipes+1)
  self.r.rng=Mock();self.r.rng.uniform.side_effect=lambda a,b:a;self.r.rng.random.return_value=1;self.r.rng.choices.return_value=[1]
  def failed(term):
   self.r.search_attempts+=1;raise NavigationLost('missing search control')
  self.r.search=Mock(side_effect=failed)
  with patch('runner.time.monotonic',side_effect=lambda:clock[0]),patch.dict('os.environ',{'IG_CONSOLE_LOCK_DIR':str(Path(self.tmp.name)/'locks')}):
   self.assertEqual(self.r.run(),'complete')
  self.r.search.assert_called_once();self.r.recover_reels.assert_called_once()
 def post(self,label='Like',selected=False,key='post'):
  return {'key':key,'author':'demo','nodes':[node(name='like-button',label=label,**({'value':'1','traits':'Selected, Button'} if selected else {}))]}
 def test_selected_liked_state_is_confirmed(self):self.assertTrue(toggle_is_active(self.post('Liked',True)['nodes'][0],'likes'))
 def test_liked_without_selection_is_not_assumed_active(self):self.assertFalse(toggle_is_active(self.post('Liked')['nodes'][0],'likes'))
 def test_delayed_like_confirmation_never_repeats_click(self):
  before=self.post();after=self.post('Liked',True);self.r.read_reel=Mock(side_effect=[before,before,after])
  self.r.toggle_on(before,'likes','like-button',{'like','like post'})
  self.r.click.assert_called_once_with('like-button');self.assertEqual(self.r.counts['likes'],1);self.assertEqual(self.r.daily['likes'],1)
 def test_already_liked_is_never_toggled_off(self):
  p=self.post('Liked',True);self.r.read_reel=Mock(return_value=p);self.r.toggle_on(p,'likes','like-button',{'like','like post'});self.r.click.assert_not_called()
 def test_unconfirmed_like_remains_reserved_and_never_replayed(self):
  p=self.post();self.r.read_reel=Mock(return_value=p)
  with self.assertRaisesRegex(RuntimeError,'无法确认'):self.r.toggle_on(p,'likes','like-button',{'like','like post'})
  self.r.toggle_on(p,'likes','like-button',{'like','like post'})
  self.r.click.assert_called_once();self.assertEqual(self.r.daily['posts']['post']['likes'],'pending')
 def test_like_on_different_post_is_not_confirmed(self):
  p=self.post();self.r.read_reel=Mock(side_effect=[p,self.post('Liked',True,'different')])
  with self.assertRaisesRegex(RuntimeError,'无法确认'):self.r.toggle_on(p,'likes','like-button',{'like','like post'})
  self.assertEqual(self.r.counts['likes'],0);self.r.click.assert_called_once()
if __name__=='__main__':unittest.main()
