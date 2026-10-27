import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,TransportLost,NavigationLost,Halt
from page_state import PageBlocked
import connections as cn
CFG=json.loads(Path('src/default-config.json').read_text())
UDID='00008030-0011223344556677';BUNDLE='com.igconsole.wda.test.xctrunner'
class ReconnectPolicy(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=1000.;self.events=[];self.delays=[]
  self.clock=patch('runner.time.monotonic',side_effect=lambda:self.now);self.clock.start();self.addCleanup(self.clock.stop)
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name,'reconnect_limit':2},notify=self.events.append)
  self.r.connect=Mock();self.r.recover_reels=Mock()
  def wait(seconds):self.delays.append(seconds);self.now+=seconds;self.r.check()
  self.r.wait=wait
 def test_three_fast_failures_then_backoff_restores_video(self):
  r=self.r;r.connect.side_effect=[TransportLost('temporary')]*3+[None]
  r.recover_connection()
  self.assertEqual(self.delays,[2,5,15]);self.assertEqual([c.kwargs['quick'] for c in r.connect.call_args_list],[True,True,True,False])
  r.recover_reels.assert_called_once();self.assertTrue(r.skip_interactions_once);self.assertEqual(r.reconnections,4)
 def test_default_policy_is_five_bounded_attempts(self):
  r=self.r;r.connect.side_effect=TransportLost('lost')
  with self.assertRaisesRegex(RuntimeError,'延后重试次数已用完'):r.recover_connection()
  self.assertEqual(r.connect.call_count,5);self.assertEqual(self.delays,[2,5,15,30])
 def test_three_backoff_slots_have_increasing_wait(self):
  r=self.r;r.cfg['reconnect_limit']=3;r.connect.side_effect=TransportLost('lost')
  with self.assertRaises(RuntimeError):r.recover_connection()
  self.assertEqual(self.delays,[2,5,15,30,60]);self.assertEqual(r.reconnections,6)
 def test_connection_success_without_video_confirmation_is_not_success(self):
  r=self.r;r.recover_reels.side_effect=[NavigationLost('loading'),None];r.recover_connection()
  self.assertEqual(r.connect.call_count,2);self.assertEqual(r.recover_reels.call_count,2)
  self.assertEqual(len([e for e in self.events if e['event']=='reconnected']),1)
 def test_unknown_draft_is_not_retried_or_cleared(self):
  r=self.r;r.recover_reels.side_effect=RuntimeError('unknown input')
  with self.assertRaisesRegex(RuntimeError,'unknown input'):r.recover_connection()
  self.assertEqual(r.connect.call_count,1)
 def test_account_check_and_fingerprint_mismatch_stop_immediately(self):
  for exc in (PageBlocked('verification'),cn.FingerprintMismatch('wrong phone')):
   r=self.r;r.connect.reset_mock();r.connect.side_effect=exc
   with self.assertRaises(type(exc)):r.recover_connection()
   self.assertEqual(r.connect.call_count,1)
 def test_stop_during_backoff_prevents_next_attempt(self):
  r=self.r;r.connect.side_effect=TransportLost('lost')
  def wait(seconds):r.stop.set();r.check()
  r.wait=wait
  with self.assertRaises(Halt):r.recover_connection()
  self.assertEqual(r.connect.call_count,1)
 def test_rolling_window_blocks_a_reconnect_storm(self):
  r=self.r;r.reconnect_times=[999]*9
  with self.assertRaisesRegex(RuntimeError,'5 分钟'):r.recover_connection()
  r.connect.assert_not_called()
 def test_window_does_not_reset_between_recovery_calls(self):
  r=self.r;r.connect.side_effect=TransportLost('lost')
  with self.assertRaises(RuntimeError):r.recover_connection()
  with self.assertRaisesRegex(RuntimeError,'5 分钟'):r.recover_connection()
  self.assertEqual(r.connect.call_count,9)
 def test_startup_unavailable_retries_but_configuration_error_does_not(self):
  r=self.r;r.connect.side_effect=[cn.WirelessStartupUnavailable('code 74'),None];r.recover_connection();self.assertEqual(r.connect.call_count,2)
  r.connect.reset_mock();r.connect.side_effect=ValueError('bad configuration')
  with self.assertRaises(ValueError):r.recover_connection()
  self.assertEqual(r.connect.call_count,1)

class FastWireless(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
  self.scopes=[patch('connections.load',return_value={'mode':'wifi','fingerprint':'pin'}),patch('device_manager.signing_record',return_value={'udid':UDID,'bundle_id':BUNDLE}),patch('device_manager.expiry_state',return_value=('valid',24)),patch('device_manager.device_dir',return_value=self.base),patch('connections.saved_wifi_endpoints',return_value=[{'url':'http://192.168.31.9:8100','preview_url':'http://192.168.31.9:9100'}])]
  for scope in self.scopes:scope.start();self.addCleanup(scope.stop)
 def test_cached_endpoint_is_verified_without_launch_or_binding_mutation(self):
  info={'url':'http://192.168.31.9:8100','fingerprint':'pin','bundle':BUNDLE,'ios':'26.6'}
  with patch('connections.checked',return_value=info) as checked,patch('connections.ensure_wifi') as full,patch('connections.save') as save,patch('controller_bridge.ensure') as launch:
   self.assertEqual(cn.quick_wifi(UDID),info['url']);checked.assert_called_once_with(info['url'],'pin',timeout=2)
   full.assert_not_called();save.assert_not_called();launch.assert_not_called()
  self.assertEqual(json.loads((self.base/'network-runtime.json').read_text())['fingerprint'],'pin')
 def test_wrong_fingerprint_never_uses_another_endpoint_or_launches(self):
  with patch('connections.checked',side_effect=cn.FingerprintMismatch('wrong')) as checked,patch('controller_bridge.ensure') as launch:
   with self.assertRaises(cn.FingerprintMismatch):cn.quick_wifi(UDID)
   checked.assert_called_once();launch.assert_not_called()
 def test_wrong_signature_is_rejected_before_session_creation(self):
  with patch('connections.checked',return_value={'fingerprint':'pin','bundle':'other'}):
   with self.assertRaises(cn.FingerprintMismatch):cn.quick_wifi(UDID)
 def test_no_endpoint_or_refused_port_is_transient_for_backoff(self):
  with patch('connections.saved_wifi_endpoints',return_value=[]):
   with self.assertRaises(cn.QuickConnectionUnavailable):cn.quick_wifi(UDID)
  with patch('connections.checked',side_effect=ConnectionRefusedError()):
   with self.assertRaises(cn.QuickConnectionUnavailable):cn.quick_wifi(UDID)
 def test_expired_or_foreign_record_does_not_contact_phone(self):
  with patch('device_manager.signing_record',return_value={'udid':'other','bundle_id':BUNDLE}),patch('connections.checked') as checked:
   with self.assertRaises(RuntimeError):cn.quick_wifi(UDID)
   checked.assert_not_called()
 def test_cancelled_fast_reconnect_never_contacts_phone(self):
  import threading
  stop=threading.Event();stop.set()
  with patch('connections.checked') as checked:
   with self.assertRaises(Halt):cn.quick_wifi(UDID,stop=stop)
   checked.assert_not_called()


class StartupClassification(unittest.TestCase):
 def test_identity_failure_is_not_retried_as_a_startup_failure(self):
  import controller_bridge
  from runner import transport_error
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'controller.log';p.write_text('controller.IdentityMismatch: Paired device mismatch\n')
   exc=controller_bridge.startup_failure(p,1)
   self.assertIsInstance(exc,cn.FingerprintMismatch);self.assertFalse(transport_error(exc))


class ReconnectNavigation(unittest.TestCase):
 def test_verified_explore_search_field_returns_without_discarding_unknown_input(self):
  import xml.etree.ElementTree as ET
  with tempfile.TemporaryDirectory() as root:
   r=Runner({**CFG,'runtime_dir':root},notify=lambda _:None)
   t=ET.Element('root');ET.SubElement(t,'XCUIElementTypeSearchField',name='search-text-input',value='',visible='true');ET.SubElement(t,'XCUIElementTypeButton',name='explore-tab',value='1',visible='true');ET.SubElement(t,'XCUIElementTypeButton',name='reels-tab',visible='true')
   r.snapshot=Mock(return_value=t);r.reels=Mock();r.read_reel=Mock(return_value={'key':'verified'})
   self.assertEqual(r.recover_reels(prefer_navigation=True),{'key':'verified'});r.reels.assert_called_once_with(initial_tree=t)
   next(e for e in t if e.tag=='XCUIElementTypeSearchField').set('value','private draft')
   r.reels.reset_mock()
   with self.assertRaises(PageBlocked):r.recover_reels(prefer_navigation=True)
   r.reels.assert_not_called()

 def test_foreground_transition_can_use_existing_safe_activation_recovery(self):
  with tempfile.TemporaryDirectory() as root:
   r=Runner({**CFG,'runtime_dir':root},notify=lambda _:None);r.wait=Mock()
   r.snapshot=Mock(side_effect=NavigationLost('not foreground'));r.read_reel=Mock(side_effect=[NavigationLost('not foreground'),{'key':'restored'}])
   r.mobile=Mock(side_effect=[{'bundleId':'com.example.other'},None])
   self.assertEqual(r.recover_reels(prefer_navigation=True),{'key':'restored'})
   self.assertIn(unittest.mock.call('activateApp',bundleId='com.burbn.instagram'),r.mobile.call_args_list)
