import json,sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path('src').resolve()))
import device_manager as dm
from setup_errors import SetupError
from device_ui import DeviceWindow

class PairingLayers(unittest.TestCase):
 def test_finder_trust_does_not_bypass_unfinished_coredevice_pairing(self):
  d={'udid':'a'*40,'ios':'17.6.1','paired':False,'developer':None,'coredevice_pairing':'unpaired'}
  with patch('runtime_manager.probe_legacy',return_value=[{'udid':d['udid'],'paired':True,'developer':True}]):dm.resolve_developer_modes([d])
  self.assertTrue(d['usb_paired']);self.assertFalse(d['paired']);self.assertTrue(d['developer'])
  with self.assertRaises(SetupError) as caught:dm.validate_device(d)
  self.assertEqual(caught.exception.code,'developer_pairing');self.assertIn('USB 信任已独立核验通过',str(caught.exception))
 def test_failed_probe_cannot_be_reported_as_proof_of_missing_phone_trust(self):
  d={'udid':'a'*40,'ios':'17.6.1','paired':False,'developer':None}
  with patch('runtime_manager.probe_legacy',side_effect=RuntimeError('timeout')):dm.resolve_developer_modes([d])
  with self.assertRaises(SetupError) as caught:dm.validate_device(d)
  self.assertNotIn('手机尚未信任此电脑',str(caught.exception));self.assertIn('尚未确认',str(caught.exception))
 def test_other_phone_proof_does_not_confirm_selected_usb_trust(self):
  d={'udid':'a'*40,'paired':False,'developer':None}
  with patch('runtime_manager.probe_legacy',return_value=[{'udid':'b'*40,'paired':True,'developer':True}]):dm.resolve_developer_modes([d])
  self.assertNotIn('usb_paired',d)

class InstalledRevalidation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);scope=patch.object(dm,'DATA',self.root);scope.start();self.addCleanup(scope.stop)
  scope=patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(self.root/'locks'));scope.start();self.addCleanup(scope.stop)
  self.d={'udid':'a'*40,'ios':'17.6.1','name':'Test','paired':True,'developer':True};self.team='ABCDE12345'
  self.r={'udid':self.d['udid'],'team_id':self.team,'bundle_id':'com.example.test.xctrunner','expires_at':(datetime.now(timezone.utc)+timedelta(days=7)).isoformat(),'controller':'modern'}
 def run_failure(self,code,installed=True,verification_error=None):
  with patch.object(dm,'_provision',side_effect=SetupError(code,'initial failure')) as install,patch.object(dm,'scan_devices',return_value=[self.d]),patch.object(dm,'profile_record',side_effect=lambda *a:dict(self.r)),patch.object(dm,'controller_apps',return_value=[{'bundle':self.r['bundle_id']}] if installed else []),patch('runtime_manager.ensure_server',return_value={'url':'http://127.0.0.1:19900/igconsole'}),patch('runner.call',side_effect=verification_error,return_value={'sessionId':'verified','capabilities':{'udid':self.d['udid']}}) as transport:
   if code not in ('developer_untrusted','automation_startup','connection') or not installed or verification_error:
    with self.assertRaises(SetupError):dm.provision(self.d,self.team)
   else:self.assertEqual(dm.provision(self.d,self.team)['bundle_id'],self.r['bundle_id'])
  install.assert_called_once();return transport
 def test_installed_transient_failure_revalidates_once_with_prebuilt_capabilities(self):
  call=self.run_failure('developer_untrusted');call.assert_called_once()
  caps=call.call_args.args[2]['capabilities']['alwaysMatch']
  self.assertTrue(caps['appium:usePrebuiltWDA']);self.assertFalse(caps['appium:useNewWDA'])
  s=dm.setup_status(self.d['udid']);self.assertEqual(s['status'],'ready');self.assertEqual(s['auto_verify_attempts'],1)
  self.assertFalse((dm.device_dir(self.d['udid'])/'pending-setup.json').exists())
 def test_real_signature_failure_is_not_retried(self):self.run_failure('signature_invalid').assert_not_called()
 def test_agreement_requirement_never_triggers_automatic_revalidation(self):self.run_failure('developer_agreement_required').assert_not_called()
 def test_missing_installed_component_never_triggers_revalidation(self):self.run_failure('developer_untrusted',installed=False).assert_not_called()
 def test_persistent_rejection_stops_after_one_verification_with_current_error(self):
  call=self.run_failure('developer_untrusted',verification_error=RuntimeError('Developer App Certificate is not trusted'));call.assert_called_once()
  s=dm.setup_status(self.d['udid']);self.assertEqual(s['status'],'awaiting_trust');self.assertEqual(s['auto_verify_attempts'],1);self.assertEqual(s['code'],'developer_untrusted')

class SetupDisplay(unittest.TestCase):
 def window(self):
  w=DeviceWindow.__new__(DeviceWindow);w.busy=False;w.status=Mock();w.last_result='old setup error';return w
 def test_verified_state_replaces_matching_stale_setup_error(self):
  w=self.window();w.refresh_setup_result('a',{'status':'failed','message':'old setup error'})
  w.refresh_setup_result('a',{'status':'ready','message':'verified'})
  self.assertEqual(w.last_result,'verified');w.status.set.assert_called_once_with('verified')
 def test_wireless_error_or_other_devices_result_is_preserved(self):
  for new_id,last in [('a','wireless preparation failed'),('b','old setup error')]:
   w=self.window();w.refresh_setup_result('a',{'status':'failed','message':'old setup error'});w.last_result=last
   w.refresh_setup_result(new_id,{'status':'ready','message':'verified'})
   self.assertEqual(w.last_result,last);w.status.set.assert_not_called()
