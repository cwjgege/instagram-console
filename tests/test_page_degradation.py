import json,sys,tempfile,unittest,zipfile,base64
from pathlib import Path
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,Halt,TransportLost
from page_state import classify,PageBlocked,profile_back_name
import diagnostics
CFG=json.loads(Path('src/default-config.json').read_text())
def screen(*nodes):
 r=ET.Element('XCUIElementTypeApplication',height='896')
 for tag,attrs in nodes:ET.SubElement(r,tag,dict(visible='true',**attrs))
 return r
def button(name,**attrs):return ('XCUIElementTypeButton',dict(name=name,**attrs))
REELS=screen(button('reels-tab',value='1'),button('like-button'),button('comment-button'))
EXPLORE=screen(button('reels-tab'),button('explore-tab',value='1'))
def search(value=''):
 return screen(('XCUIElementTypeTextView',dict(name='search-bar-text-view',value=value,y='57',height='32')),
  ('XCUIElementTypeOther',dict(name='search-bar-with-cancel')),button('search-bar-back-button'))
class Degradation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.cfg={**CFG,'runtime_dir':str(Path(self.tmp.name).resolve())};self.events=[]
  self.r=Runner(self.cfg,notify=self.events.append);self.r.wait=Mock();self.r.click=Mock()
 def tearDown(self):self.tmp.cleanup()
 def test_classifies_reels_explore_input_unknown_and_alert(self):
  self.assertEqual(classify(REELS).kind,'reels');self.assertEqual(classify(EXPLORE).kind,'explore')
  self.assertEqual(classify(search()).kind,'search_input');self.assertEqual(classify(screen()).kind,'unknown')
  self.assertTrue(classify(screen(('XCUIElementTypeAlert',{}))).blocked)
 def test_caption_about_verification_does_not_block_normal_video(self):
  t=ET.fromstring(ET.tostring(REELS));caption=ET.SubElement(t,'XCUIElementTypeOther',dict(visible='true',name='reel-caption'))
  ET.SubElement(caption,'XCUIElementTypeStaticText',dict(visible='true',label='Confirm your identity'))
  self.assertEqual(classify(t).kind,'reels')
 def profile(self):
  return screen(('XCUIElementTypeCollectionView',dict(name='profile')),button('profile-back-button',y='48'),button('profile-more-bar-button'))
 def test_profile_returns_only_through_observed_back(self):
  self.assertEqual(classify(self.profile()).kind,'profile')
  self.r.snapshot=Mock(side_effect=[self.profile(),REELS]);self.r.return_from_search()
  self.r.click.assert_called_once_with('profile-back-button')
 def test_profile_back_is_not_accepted_without_context_or_if_duplicated(self):
  self.assertIsNone(profile_back_name(screen(button('profile-back-button'))))
  t=self.profile();ET.SubElement(t,'XCUIElementTypeButton',dict(visible='true',name='profile-back-button',y='48'))
  self.assertIsNone(profile_back_name(t))
 def test_profile_with_input_preserves_draft(self):
  t=self.profile();ET.SubElement(t,'XCUIElementTypeTextView',dict(visible='true',value='my draft'))
  self.r.snapshot=Mock(return_value=t)
  with self.assertRaises(PageBlocked):self.r.return_from_search()
  self.r.click.assert_not_called()
 def test_search_input_never_clears_unknown_draft(self):
  self.r.snapshot=Mock(return_value=search('my actual draft'));self.r.command=Mock()
  with self.assertRaises(PageBlocked):self.r.search_input()
  self.r.command.assert_not_called()
 def test_classic_own_query_recovers_but_unknown_query_is_preserved(self):
  for value,allowed in [('AI video creation',True),('my actual draft',False)]:
   t=screen(('XCUIElementTypeSearchField',dict(name='search-text-input',value=value)),button('Cancel'))
   self.r.snapshot=Mock(return_value=t);self.r.read_reel=Mock(side_effect=[NavigationLost('search'),{'key':'new'}]);self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.click.reset_mock()
   if allowed:
    self.r.recover_reels();self.r.click.assert_called_once_with('Cancel')
   else:
    with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
    self.r.click.assert_not_called()
 def test_known_sheet_with_unique_dismiss_can_return(self):
  t=screen(('XCUIElementTypeOther',dict(name='ig-partial-modal-sheet-view-controller-content')),button('dismiss',label='Dismiss'))
  self.r.snapshot=Mock(side_effect=[t,REELS]);self.r.command=Mock(return_value=[{'element-6066-11e4-a52e-4f735466cecf':'close'}])
  self.r.return_from_search();self.assertEqual(self.r.command.call_args_list[-1].args,('POST','/element/close/click',{}))
 def test_sheet_with_unknown_input_never_dismisses(self):
  t=screen(('XCUIElementTypeOther',dict(name='ig-partial-modal-sheet-view-controller-content')),button('dismiss',label='Dismiss'),('XCUIElementTypeTextView',dict(value='my draft')))
  self.r.snapshot=Mock(return_value=t);self.r.command=Mock()
  with self.assertRaises(PageBlocked):self.r.return_from_search()
  self.r.command.assert_not_called()
 def test_navigation_transport_loss_can_reconnect_but_social_click_is_not_replayed(self):
  # Use the real click method rather than the navigation mock from setUp.
  self.r.find=Mock(return_value='button');self.r.command=Mock(side_effect=RuntimeError('socket hang up'))
  for name in ('profile-back-button','search-bar-back-button','Back','Cancel'):
   with self.assertRaises(TransportLost):Runner.click(self.r,name)
  with self.assertRaises(RuntimeError):Runner.click(self.r,'like-button')
  self.assertEqual(self.r.command.call_count,5)
 def explore_with_search(self):
  return screen(('XCUIElementTypeSearchField',dict(name='search-text-input',y='50',height='44')),button('explore-tab',value='1'),button('reels-tab'))
 def test_explore_with_inactive_search_field_is_not_focused_search(self):
  state=classify(self.explore_with_search());self.assertEqual(state.kind,'explore');self.assertTrue(state.search_input)
 def test_return_from_focused_search_via_explore_with_search_box(self):
  self.r.snapshot=Mock(side_effect=[search(),self.explore_with_search(),REELS]);self.r.return_from_search()
  self.assertEqual(self.r.click.call_args_list,[unittest.mock.call('search-bar-back-button'),unittest.mock.call('reels-tab')])
 def test_unknown_query_is_preserved_as_input(self):
  self.assertEqual(classify(search('private draft'),['AI video creation']).kind,'input_unknown')
 def test_login_and_verification_override_video_markers(self):
  for tag,attrs in [('XCUIElementTypeSecureTextField',{}),('XCUIElementTypeStaticText',dict(label='Confirm your identity'))]:
   t=ET.fromstring(ET.tostring(REELS));ET.SubElement(t,tag,dict(visible='true',**attrs))
   with self.assertRaises(PageBlocked):self.r.observe(t)
 def test_search_failure_disables_only_search_and_uses_separate_budget(self):
  self.r.search=Mock(side_effect=NavigationLost('missing input'));self.r.snapshot=Mock(side_effect=[search(),EXPLORE,REELS])
  self.r.recovery_times=[0]*5
  self.assertFalse(self.r.search_optional('AI video creation'));self.assertFalse(self.r.feature_state['search']['enabled'])
  self.assertEqual(self.r.click.call_args_list,[unittest.mock.call('search-bar-back-button'),unittest.mock.call('reels-tab')])
  self.assertEqual(self.r.recoveries,0);self.assertEqual(len(self.r.recovery_times),5)
  self.r.search_optional('another query');self.r.search.assert_called_once()
  self.assertTrue(any(e['event']=='search_resumed' for e in self.events))
 def test_new_run_reenables_search(self):
  self.r.feature_state['search']['enabled']=False
  self.assertTrue(Runner(self.cfg,notify=Mock()).feature_state['search']['enabled'])
 def test_search_api_lookup_error_also_degrades(self):
  self.r.search=Mock(side_effect=RuntimeError('No such element'));self.r.snapshot=Mock(return_value=REELS)
  self.assertFalse(self.r.search_optional('AI video creation'));self.assertEqual(self.r.search_failures,1)
 def test_search_success_does_not_disable_feature(self):
  self.r.search=Mock();self.assertTrue(self.r.search_optional('AI video creation'));self.assertTrue(self.r.feature_state['search']['enabled'])
 def test_search_never_swallows_stop_or_security(self):
  for exc in [Halt('stop'),PageBlocked('verification')]:
   self.r.search=Mock(side_effect=exc);self.r.return_from_search=Mock()
   with self.assertRaises(type(exc)):self.r.search_optional('AI video creation')
   self.r.return_from_search.assert_not_called()
 def test_transport_error_disables_search_and_reaches_reconnect(self):
  self.r.search=Mock(side_effect=TransportLost('lost'));self.r.return_from_search=Mock()
  with self.assertRaises(TransportLost):self.r.search_optional('AI video creation')
  self.assertFalse(self.r.feature_state['search']['enabled']);self.r.return_from_search.assert_not_called()
 def test_unknown_input_is_not_dismissed(self):
  self.r.snapshot=Mock(return_value=search('a private draft'))
  with self.assertRaises(PageBlocked):self.r.return_from_search()
  self.r.click.assert_not_called()
 def test_unknown_page_has_no_blind_clicks(self):
  self.r.snapshot=Mock(return_value=screen())
  with self.assertRaises(RuntimeError):self.r.return_from_search()
  self.r.click.assert_not_called()
 def test_return_budget_is_bounded(self):
  self.r.snapshot=Mock(return_value=EXPLORE)
  with self.assertRaisesRegex(RuntimeError,'尝试已用完'):self.r.return_from_search()
  self.assertEqual(self.r.click.call_count,5)
 def test_disabled_recovery_keeps_search_page(self):
  self.r.cfg['recovery_enabled']=False
  with self.assertRaises(RuntimeError):self.r.return_from_search()
  self.r.click.assert_not_called()
 def test_diagnostic_export_excludes_private_content_and_keys(self):
  secret='PRIVATE-SENTINEL'
  t=screen(('XCUIElementTypeTextView',dict(name='search-bar-text-view',value=secret)),('XCUIElementTypeStaticText',dict(name=secret,label=secret)))
  diagnostics.record(self.r.log_dir,'search',RuntimeError(secret),t,classify(t),self.r.feature_state,self.r.counts)
  (Path(self.cfg['runtime_dir'])/'config.json').write_text(secret)
  (self.r.log_dir/'events.jsonl').write_text(secret)
  (self.r.log_dir/'summary.json').write_text(json.dumps(dict(status='complete',device_udid=secret,counts=self.r.counts)))
  z=Path(self.tmp.name)/'diagnostics.zip';result=diagnostics.export(self.cfg['runtime_dir'],z)
  with zipfile.ZipFile(z) as f:
   raw=''.join(f.read(n).decode() for n in f.namelist());self.assertNotIn(secret,raw);self.assertNotIn('value=',raw)
   self.assertIn('search-bar-text-view',raw);self.assertEqual(result['cases'],1)
 def test_diagnostics_are_bounded_and_screenshots_opt_in(self):
  image=base64.b64encode(b'\x89PNG\r\n\x1a\nFAKE').decode()
  for _ in range(8):diagnostics.record(self.r.log_dir,'search',RuntimeError(),REELS,classify(REELS),self.r.feature_state,{},image)
  self.assertEqual(len(list((self.r.log_dir/'diagnostics').glob('*.json'))),5)
  for include in [False,True]:
   z=Path(self.tmp.name)/('export-'+str(include)+'.zip');diagnostics.export(self.cfg['runtime_dir'],z,include)
   with zipfile.ZipFile(z) as f:self.assertEqual(any(n.endswith('.png') for n in f.namelist()),include)
 def test_jpeg_screenshot_fallback_is_preserved_only_when_requested(self):
  image=base64.b64encode(b'\xff\xd8\xffFAKE').decode()
  diagnostics.record(self.r.log_dir,'search',RuntimeError(),REELS,classify(REELS),self.r.feature_state,{},image)
  for include in [False,True]:
   z=Path(self.tmp.name)/('jpeg-'+str(include)+'.zip');diagnostics.export(self.cfg['runtime_dir'],z,include)
   with zipfile.ZipFile(z) as f:self.assertEqual(any(n.endswith('.jpg') for n in f.namelist()),include)
 def test_export_does_not_follow_case_symlinks(self):
  d=self.r.log_dir/'diagnostics';d.mkdir();secret=Path(self.tmp.name)/'secret';secret.write_text('do not export')
  (d/'case-01.json').symlink_to(secret);z=Path(self.tmp.name)/'export.zip';diagnostics.export(self.cfg['runtime_dir'],z)
  with zipfile.ZipFile(z) as f:self.assertNotIn('diagnostics/case-01.json',f.namelist())
 def test_failed_search_continues_main_loop_without_retry(self):
  clock=[0.0];n=[0];r=self.r;r.cfg.update(device_udid='A'*20,max_swipes_per_run=50,enable_comments=False);r.seconds=130
  r.wait=lambda s:clock.__setitem__(0,clock[0]+s);r.connect=Mock();r.reels=Mock();r.snapshot=Mock(return_value=REELS)
  def post():n[0]+=1;return dict(key=str(n[0]),author='demo',caption='unrelated',nodes=[])
  r.read_reel=post;r.swipe=lambda _:setattr(r,'swipes',r.swipes+1)
  r.rng=Mock();r.rng.uniform.side_effect=lambda a,b:a;r.rng.random.return_value=1;r.rng.choices.return_value=[1]
  before=[]
  def fail(term):before.append(r.swipes);raise NavigationLost('missing search')
  r.search=Mock(side_effect=fail)
  with patch('runner.time.monotonic',side_effect=lambda:clock[0]),patch.dict('os.environ',{'IG_CONSOLE_LOCK_DIR':str(Path(self.tmp.name)/'locks')}):self.assertEqual(r.run(),'complete')
  r.search.assert_called_once();self.assertGreater(r.swipes,before[0]);self.assertFalse(r.feature_state['search']['enabled'])
if __name__=='__main__':unittest.main()
