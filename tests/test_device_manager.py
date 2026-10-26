import json,sys,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path('src').resolve()))
import device_manager as dm

class DeviceSetup(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.data=Path(self.tmp.name).resolve()
  self.scope=patch.object(dm,'DATA',self.data);self.scope.start()
  self.env=patch.dict('os.environ',{'IG_CONSOLE_LOCK_DIR':str(self.data/'locks')});self.env.start()
  self.device=dict(udid='00008030-0000000000000000',identifier='core-id',name='Test iPhone',ios='18.1',paired=True,developer=True)
 def tearDown(self):self.env.stop();self.scope.stop();self.tmp.cleanup()
 def test_only_wired_iphones_are_selectable(self):
  def row(kind,transport):return {'identifier':'one','hardwareProperties':{'deviceType':kind,'udid':self.device['udid']},'connectionProperties':{'transportType':transport,'pairingState':'paired'},'deviceProperties':{'name':'Test','developerModeStatus':'enabled'}}
  devices=dm.wired_iphones({'devices':[row('iPhone','wired'),row('iPhone','localNetwork'),row('appleWatch','wired')]})
  self.assertEqual(len(devices),1);self.assertTrue(devices[0]['developer'])
 def test_payload_is_per_device_and_uses_selected_team(self):
  c=dm.make_payload(self.device,'ABCDE12345')['capabilities']['alwaysMatch']
  self.assertEqual(c['appium:udid'],self.device['udid']);self.assertEqual(c['appium:xcodeOrgId'],'ABCDE12345')
  self.assertTrue(c['appium:derivedDataPath'].startswith(str(self.data)))
  self.assertNotIn('password',json.dumps(c).lower());self.assertTrue(c['appium:noReset'])
 def test_provision_build_request_never_requests_phone_app_uninstall(self):
  record={'expires_at':(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()}
  with patch.object(dm,'scan_devices',return_value=[self.device]),patch.object(dm,'check_xcode'),patch('runtime_manager.ensure_server',return_value={'url':'http://127.0.0.1:19000/igconsole'}),patch.object(dm,'close_other_sessions'),patch.object(dm,'stop_controller_for_install') as stop_old,patch.object(dm,'profile_record',return_value=record),patch('runner.call',return_value={'sessionId':'new'}) as call:
   dm.provision(self.device,'ABCDE12345')
  stop_old.assert_called_once()
  caps=call.call_args.args[2]['capabilities']['alwaysMatch']
  self.assertFalse(caps['appium:useNewWDA']);self.assertFalse(caps['appium:usePrebuiltWDA'])
 def test_reject_path_injection(self):
  with self.assertRaises(ValueError):dm.device_dir('../../other')
  with self.assertRaises(ValueError):dm.make_payload(self.device,'$(id)')
 def test_expiration_is_based_on_real_timestamp(self):
  now=datetime.now(timezone.utc)
  for hours,state in [(120,'valid'),(24,'soon'),(-1,'expired')]:
   self.assertEqual(dm.expiry_state({'expires_at':(now+timedelta(hours=hours)).isoformat()},48,now)[0],state)
  self.assertEqual(dm.expiry_state(None)[0],'unknown')
  self.assertEqual(dm.expiry_state({'expires_at':'invalid'})[0],'unknown')
 def test_naive_timestamp_is_treated_as_utc(self):
  now=datetime.now(timezone.utc)
  r={'expires_at':(now+timedelta(hours=20)).replace(tzinfo=None).isoformat()}
  self.assertEqual(dm.expiry_state(r,48,now)[0],'soon')
 def test_disconnected_or_wrong_device_never_installs(self):
  with patch.object(dm,'scan_devices',return_value=[]),patch.object(dm,'ensure_server') as server:
   with self.assertRaisesRegex(RuntimeError,'USB'):dm.provision(self.device,'ABCDE12345')
   server.assert_not_called()
 def test_developer_mode_gate_never_installs(self):
  with patch.object(dm,'scan_devices',return_value=[{**self.device,'developer':False}]),patch.object(dm,'ensure_server') as server:
   with self.assertRaisesRegex(RuntimeError,'开发者模式'):dm.provision(self.device,'ABCDE12345')
   server.assert_not_called()
 def test_pairing_gate_never_installs(self):
  with patch.object(dm,'scan_devices',return_value=[{**self.device,'paired':False}]),patch.object(dm,'ensure_server') as server:
   with self.assertRaisesRegex(RuntimeError,'信任'):dm.provision(self.device,'ABCDE12345')
   server.assert_not_called()
 def test_runtime_copy_is_relocatable_and_preserves_executable(self):
  assets=self.data/'assets';(assets/'vendor/bin').mkdir(parents=True)
  binary=assets/'vendor/bin/node';binary.write_text('test');binary.chmod(0o755)
  with patch.object(dm,'ASSETS',assets):
   location=dm.prepare_vendor()
   self.assertTrue((location/'.ready').exists());self.assertEqual((location/'bin/node').read_text(),'test')
   self.assertTrue((location/'bin/node').stat().st_mode&0o100)
 def test_atomic_storage_and_no_record(self):
  path=self.data/'nested/signing.json';dm.write_json(path,{'expires_at':'test'})
  self.assertEqual(json.loads(path.read_text()),{'expires_at':'test'})
  self.assertFalse(path.with_suffix('.json.tmp').exists())
  self.assertIsNone(dm.signing_record(self.device['udid']))
 def test_xcode_first_launch_is_required(self):
  with patch.object(dm,'xcode_directory',return_value=self.data),patch.object(dm,'child_env',return_value={}),patch.object(dm.subprocess,'run',return_value=Mock(returncode=1)):
   with self.assertRaisesRegex(RuntimeError,'首次设置'):dm.check_xcode()
 def test_signature_banner_and_one_time_warning(self):
  from panel import Panel
  panel=Panel.__new__(Panel);panel.cfg={'device_udid':self.device['udid'],'signature_warn_hours':48}
  panel.signature_status=Mock();panel.status=Mock();panel.root=Mock();panel.signature_notices=set()
  record={'udid':self.device['udid'],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()}
  with patch('panel.FROZEN',True),patch.object(dm,'signing_record',return_value=record),patch('panel.messagebox.showwarning') as warning:
   panel.update_signature();panel.update_signature()
   warning.assert_called_once();self.assertIn('即将到期',panel.signature_status.set.call_args.args[0])
 def test_starting_one_device_preserves_other_sessions(self):
  old=self.data/'devices/two/active-session.json';dm.write_json(old,{'sessionId':'old'})
  with patch('runner.call') as call:
   self.assertEqual(dm.close_other_sessions('keep'),0)
   call.assert_not_called();self.assertTrue(old.exists())
 def test_stale_peer_session_is_not_touched(self):
  old=self.data/'devices/one/active-session.json';dm.write_json(old,{'sessionId':'old'})
  with patch('runner.call') as call:
   dm.close_other_sessions();call.assert_not_called();self.assertTrue(old.exists())
 def test_shutdown_never_kills_an_unrelated_process(self):
  with patch.object(dm,'SERVER_PROCESS',None),patch.object(dm,'SERVER_PID',1234),patch.object(dm.subprocess,'run',return_value=Mock(stdout='/unrelated/program')),patch.object(dm.os,'killpg') as kill:
   dm.stop_owned_server();kill.assert_not_called()
 def test_one_physical_device_cannot_be_claimed_twice(self):
  from app_paths import lock_device
  with lock_device(self.device['udid']):
   with self.assertRaisesRegex(RuntimeError,'另一个控制任务'):lock_device(self.device['udid'])
  with lock_device(self.device['udid']):pass

if __name__=='__main__':unittest.main()

class DeveloperModeDetection(unittest.TestCase):
 def device(self,**kwargs):return {'udid':'00008030-0011223344556677','paired':True,'developer':None,**kwargs}
 def test_missing_coredevice_field_is_unknown_instead_of_disabled(self):
  rows={'devices':[{'identifier':'core','connectionProperties':{'transportType':'wired','pairingState':'paired'},'hardwareProperties':{'deviceType':'iPhone','udid':'00008030-0011223344556677'},'deviceProperties':{'osVersionNumber':'27.0.1'}}]}
  self.assertIsNone(dm.wired_iphones(rows)[0]['developer'])
 def test_lockdown_true_resolves_missing_xcode_field(self):
  d=self.device()
  with patch('runtime_manager.probe_legacy',return_value=[{**d,'developer':True}]):dm.resolve_developer_modes([d])
  self.assertIs(d['developer'],True);self.assertEqual(d['developer_source'],'lockdown')
 def test_other_phone_cannot_resolve_selected_phone(self):
  d=self.device()
  with patch('runtime_manager.probe_legacy',return_value=[self.device(udid='00008030-008899AABBCCDDEE',developer=True)]):dm.resolve_developer_modes([d])
  self.assertIsNone(d['developer'])
 def test_failed_or_empty_probe_remains_unknown(self):
  for result in ([],[self.device(developer=None)],[self.device(developer=True,paired=False)]):
   d=self.device()
   with patch('runtime_manager.probe_legacy',return_value=result):dm.resolve_developer_modes([d])
   self.assertIsNone(d['developer'])
  d=self.device()
  with patch('runtime_manager.probe_legacy',side_effect=RuntimeError('locked')):dm.resolve_developer_modes([d])
  self.assertIsNone(d['developer'])
 def test_explicit_disabled_is_preserved_and_does_not_probe(self):
  d=self.device(developer=False)
  with patch('runtime_manager.probe_legacy') as probe:dm.resolve_developer_modes([d])
  self.assertIs(d['developer'],False);probe.assert_not_called()
 def test_unknown_cannot_install_and_does_not_claim_mode_disabled(self):
  with self.assertRaises(dm.SetupError) as caught:dm.validate_device(self.device(ios='27.0.1'))
  self.assertEqual(caught.exception.code,'developer_mode_unconfirmed');self.assertIn('未读到状态不代表未开启',str(caught.exception))
 def test_readback_false_is_not_treated_as_unknown(self):
  d=self.device()
  with patch('runtime_manager.probe_legacy',return_value=[self.device(developer=False)]):dm.resolve_developer_modes([d])
  self.assertIs(d['developer'],False)

class ComponentReplacement(unittest.TestCase):
 def test_only_the_selected_installed_controller_process_is_stopped(self):
  d={'udid':'00008030-0011223344556677','ios':'27.0.1'};bundle='com.example.wda.xctrunner'
  apps=[{'bundle':bundle,'path':'/private/Apps/Owned.app'}]
  processes={'runningProcesses':[{'processIdentifier':7,'executable':'file:///private/Apps/Owned.app/Runner'},{'processIdentifier':8,'executable':'file:///private/Apps/Other.app/Runner'}]}
  with patch.object(dm,'controller_apps',return_value=apps),patch.object(dm,'coredevice',side_effect=[processes,{}]) as command:dm.stop_controller_for_install(d,bundle)
  self.assertEqual(command.call_args_list[1].args[0][-2:],['--pid','7']);self.assertEqual(command.call_count,2)
 def test_no_process_is_stopped_without_matching_bundle_path(self):
  with patch.object(dm,'controller_apps',return_value=[{'bundle':'other','path':'/private/Apps/Other.app'}]),patch.object(dm,'coredevice') as command:
   dm.stop_controller_for_install({'udid':'0'*40,'ios':'27.0.1'},'com.example.wda')
  command.assert_not_called()
