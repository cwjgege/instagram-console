import json,sys,tempfile,unittest,time
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,Halt
CFG=json.loads(Path('src/default-config.json').read_text())
def screen(*nodes):
 root=ET.Element('root')
 for tag,attrs in nodes:ET.SubElement(root,tag,{'visible':'true',**attrs})
 return root
EXPLORE=screen(('XCUIElementTypeButton',{'name':'reels-tab'}))
REELS_EMPTY=screen(('XCUIElementTypeButton',{'name':'reels-tab','value':'1'}))
VALID=screen(('XCUIElementTypeButton',{'name':'reels-tab','value':'1'}),('XCUIElementTypeButton',{'name':'like-button'}),('XCUIElementTypeButton',{'name':'comment-button'}),('XCUIElementTypeButton',{'label':'Post by creator'}),('XCUIElementTypeOther',{'name':'ads-caption-button'}))
class Recovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.cfg={**CFG,'runtime_dir':str(Path(self.tmp.name).resolve())};self.events=[]
  self.r=Runner(self.cfg,notify=self.events.append);self.r.wait=lambda _:None
  self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.click=Mock();self.r.gesture=Mock()
  self.r.snapshot=lambda:EXPLORE
 def tearDown(self):self.tmp.cleanup()
 def lost(self):raise NavigationLost('lost')
 def test_reenter_reels_and_reset_history(self):
  post={'key':'new'};self.r.read_reel=Mock(side_effect=[NavigationLost('lost'),post]);self.r.position=5
  self.assertEqual(self.r.recover_reels(),post);self.r.click.assert_called_once_with('reels-tab')
  self.assertEqual(self.r.position,0);self.assertEqual(self.r.recoveries,1);self.assertEqual(self.r.navigation_epoch,1)
 def test_bounded_refresh_and_vertical_only(self):
  self.r.snapshot=lambda:REELS_EMPTY;self.r.read_reel=self.lost
  with self.assertRaises(RuntimeError):self.r.recover_reels()
  self.assertEqual(self.r.click.call_count,2)
  self.r.gesture.assert_called_once_with('down')
 def test_draft_preserved(self):
  self.r.read_reel=self.lost;self.r.snapshot=lambda:screen(('XCUIElementTypeTextView',{'name':'text-view','value':'draft'}),('XCUIElementTypeButton',{'name':'reels-tab'}))
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def business_chat(self,value='Hello, can I get more info on this?',keyboard=False):
  tree=screen(('XCUIElementTypeTextField',{'value':value}),
   ('XCUIElementTypeStaticText',{'label':'Business chat · example'}),
   ('XCUIElementTypeStaticText',{'name':'Have a question?\nChat with us'}),
   ('XCUIElementTypeButton',{'name':'Send Message'}),
   ('XCUIElementTypeButton',{'name':'ig icon x pano outline 24'}),
   ('XCUIElementTypeButton',{'name':'reels-tab','value':'1'}))
  if keyboard:ET.SubElement(tree,'XCUIElementTypeKeyboard',{'visible':'true'})
  return tree
 def test_known_ad_endcard_closes_only_x(self):
  self.r.snapshot=lambda:self.business_chat();self.r.read_reel=Mock(side_effect=[NavigationLost('ad'),{'key':'new'}])
  self.r.recover_reels();self.r.click.assert_called_once_with('ig icon x pano outline 24')
 def test_modified_ad_inquiry_stays_untouched(self):
  self.r.snapshot=lambda:self.business_chat('My actual inquiry');self.r.read_reel=self.lost
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def test_ad_endcard_with_keyboard_stays_untouched(self):
  self.r.snapshot=lambda:self.business_chat(keyboard=True);self.r.read_reel=self.lost
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def test_ad_dismiss_can_be_disabled(self):
  self.r.cfg['close_business_chat']=False
  self.r.snapshot=lambda:self.business_chat();self.r.read_reel=self.lost
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def test_verification_not_dismissed(self):
  self.r.read_reel=self.lost;self.r.snapshot=lambda:screen(('XCUIElementTypeStaticText',{'label':'Confirm your identity'}),('XCUIElementTypeButton',{'name':'reels-tab'}))
  with self.assertRaisesRegex(RuntimeError,'验证'):self.r.recover_reels()
  self.r.click.assert_not_called()
 def test_unknown_page_no_blind_gestures(self):
  self.r.read_reel=self.lost;self.r.snapshot=lambda:screen()
  with self.assertRaisesRegex(RuntimeError,'可靠'):self.r.recover_reels()
  self.r.click.assert_not_called();self.assertFalse(any(c.args[0]=='swipe' for c in self.r.mobile.call_args_list))
 def test_disabled_or_run_limit(self):
  self.r.cfg['recovery_enabled']=False
  with self.assertRaises(RuntimeError):self.r.recover_reels()
  self.r.cfg['recovery_enabled']=True;self.r.recovery_times=[time.monotonic()]*self.r.cfg['recovery_run_limit']
  with self.assertRaises(RuntimeError):self.r.recover_reels()
  self.r.mobile.assert_not_called()
 def test_old_recoveries_do_not_exhaust_current_window(self):
  self.r.recoveries=20;self.r.recovery_times=[time.monotonic()-301]*5
  self.r.read_reel=Mock(return_value={'key':'new'})
  self.r.recover_reels();self.assertEqual(self.r.recoveries,21)
  self.assertEqual(len(self.r.recovery_times),1)
 def test_stop_has_priority(self):
  self.r.stop.set()
  with self.assertRaises(Halt):self.r.recover_reels()
  self.r.mobile.assert_not_called()
 def test_app_reactivation_once_then_validate(self):
  self.r.read_reel=Mock(side_effect=[NavigationLost('away'),{'key':'new'}]);self.r.mobile.return_value={'bundleId':'com.apple.springboard'}
  self.r.recover_reels();self.r.mobile.assert_any_call('activateApp',bundleId='com.burbn.instagram')
 def test_read_reel_does_not_accept_feed(self):
  feed=screen(('XCUIElementTypeButton',{'name':'like-button'}),('XCUIElementTypeButton',{'name':'comment-button'}))
  self.r.snapshot=lambda:feed
  with self.assertRaises(NavigationLost):self.r.read_reel()
 def test_loading_retries_without_clicks(self):
  self.r.snapshot=Mock(side_effect=[REELS_EMPTY,VALID]);self.r.read_reel();self.r.click.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
