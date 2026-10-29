import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path('src').resolve()))
import device_manager as dm
from setup_errors import SetupError,classify_setup_error,log_positions,attempt_text

class FailureClassification(unittest.TestCase):
 def test_device_limit_wins_over_secondary_build_and_expired_profile_errors(self):
  issue=classify_setup_error(RuntimeError('xcodebuild failed code 65'),'Your development team has reached the maximum number of registered iPhone devices.\nprovisioning profile has expired\n** TEST BUILD FAILED **')
  self.assertEqual(issue.code,'device_limit');self.assertIn('名额已满',str(issue));self.assertIn('不会立即释放',str(issue))
 def test_exact_trust_rejection_wins_over_secondary_tool_error(self):
  issue=classify_setup_error(RuntimeError('xcodebuild code 65'),'Developer App Certificate is not trusted. xcrun: error: unable to find utility devicectl')
  self.assertEqual(issue.code,'developer_untrusted');self.assertIn('重新验证',str(issue))
 def test_generic_code_65_is_not_assumed_to_be_a_certificate_issue(self):
  self.assertEqual(classify_setup_error(RuntimeError('xcodebuild failed with code 65')).code,'unknown')
 def test_ambiguous_signature_error_does_not_claim_trust_is_the_only_cause(self):
  self.assertEqual(classify_setup_error(RuntimeError('invalid code signature, inadequate entitlements or its profile has not been explicitly trusted by the user')).code,'signature_or_trust')
 def test_real_build_failure_wins_over_proxy_socket_noise(self):
  self.assertEqual(classify_setup_error(RuntimeError('code 65'),'socket hang up\n** TEST BUILD FAILED **').code,'build_failed')
 def test_expired_and_missing_signing_keys_are_distinguished(self):
  self.assertEqual(classify_setup_error(RuntimeError('provisioning profile has expired')).code,'signature_invalid')
  self.assertEqual(classify_setup_error(RuntimeError('No signing certificate Apple Development found')).code,'signing_failed')
 def test_old_trust_error_cannot_contaminate_new_attempt(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);file=root/'appium.log';file.write_text('Developer App Certificate is not trusted\n');positions=log_positions(root)
   with file.open('a') as out:out.write('** TEST BUILD FAILED **\n')
   text=attempt_text(root,positions);self.assertNotIn('not trusted',text)
   self.assertEqual(classify_setup_error(RuntimeError('code 65'),text).code,'build_failed')
 def test_automation_timeout_explains_device_preparation(self):
  self.assertEqual(classify_setup_error(RuntimeError('Timed out while enabling automation mode.')).code,'automation_timeout')

class ResumeSetup(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.patch=patch.object(dm,'DATA',self.root);self.patch.start()
  self.env=patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(self.root/'locks'));self.env.start()
  self.device={'udid':'a'*40,'ios':'18.1','name':'Test','paired':True,'developer':True};self.team='ABCDE12345';self.runtime=dm.device_dir(self.device['udid']);self.runtime.mkdir(parents=True)
  self.record={'team_id':self.team,'bundle_id':'com.example.test.xctrunner','controller':'modern','expires_at':(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()}
 def tearDown(self):self.env.stop();self.patch.stop();self.temp.cleanup()
 def test_failed_trust_creates_pending_record_but_not_a_valid_install(self):
  def fail():raise RuntimeError('Developer App Certificate is not trusted')
  with patch.object(dm,'profile_record',return_value=self.record),patch.object(dm,'controller_apps',return_value=[{'bundle':self.record['bundle_id']}]):
   with self.assertRaises(SetupError):dm.setup_attempt(self.device,self.team,fail)
  self.assertEqual(dm.setup_status(self.device['udid'])['status'],'awaiting_trust')
  self.assertIsNone(dm.signing_record(self.device['udid']));self.assertTrue(dm.setup_blocker(self.device['udid']))
 def test_verify_reuses_existing_install_without_build_or_install(self):
  dm.write_json(self.runtime/'pending-setup.json',{'team_id':self.team,'record':self.record})
  with patch.object(dm,'scan_devices',return_value=[self.device]),patch.object(dm,'profile_record',return_value=dict(self.record)),patch.object(dm,'controller_apps',return_value=[{'bundle':self.record['bundle_id']}]),patch('runtime_manager.ensure_server',return_value={'url':'http://127.0.0.1:19000/igconsole'}),patch('runtime_manager.build_legacy') as build,patch('runtime_manager.ensure_legacy') as legacy,patch('runner.call',return_value={'sessionId':'new','capabilities':{'udid':self.device['udid']}}) as call:
   dm.verify_provision(self.device)
  build.assert_not_called();legacy.assert_not_called();caps=call.call_args.args[2]['capabilities']['alwaysMatch']
  self.assertTrue(caps['appium:usePrebuiltWDA']);self.assertFalse(caps['appium:useNewWDA'])
  self.assertIsNone(dm.setup_blocker(self.device['udid']));self.assertFalse((self.runtime/'pending-setup.json').exists())
 def test_no_app_does_not_create_successful_install_or_session(self):
  dm.write_json(self.runtime/'pending-setup.json',{'team_id':self.team,'record':self.record})
  with patch.object(dm,'scan_devices',return_value=[self.device]),patch.object(dm,'profile_record',return_value=dict(self.record)),patch.object(dm,'controller_apps',return_value=[]),patch('runner.call') as call:
   with self.assertRaisesRegex(SetupError,'未找到'):dm.verify_provision(self.device)
  call.assert_not_called();self.assertIsNone(dm.signing_record(self.device['udid']))
 def test_unsupported_older_os_gets_compatibility_message(self):
  with self.assertRaisesRegex(SetupError,'iOS 16'):dm.validate_device({**self.device,'ios':'15.8'})
 def test_paired_is_not_the_same_as_developer_app_trust(self):
  dm.validate_device(self.device)
  self.assertIsNone(dm.signing_record(self.device['udid']))

class LegacyResumeSetup(unittest.TestCase):
 setUp=ResumeSetup.setUp
 tearDown=ResumeSetup.tearDown
 def test_legacy_revalidation_restarts_bridge_without_reinstall(self):
  self.device['ios']='16.7';self.record['controller']='legacy16';dm.write_json(self.runtime/'pending-setup.json',{'team_id':self.team,'record':self.record})
  with patch.object(dm,'scan_devices',return_value=[self.device]),patch.object(dm,'profile_record',return_value=dict(self.record)),patch.object(dm,'controller_apps',return_value=[{'bundle':self.record['bundle_id']}]),patch('runtime_manager.ensure_server',return_value={'url':'http://127.0.0.1:19000/igconsole'}),patch('runtime_manager.build_legacy') as build,patch('runtime_manager.ensure_legacy',return_value='http://127.0.0.1:19001') as legacy,patch('runner.call',return_value={'sessionId':'new','capabilities':{'udid':self.device['udid']}}) as call:
   dm.verify_provision(self.device)
  build.assert_not_called();self.assertNotIn('install',legacy.call_args.kwargs)
  self.assertEqual(call.call_args.args[2]['capabilities']['alwaysMatch']['appium:webDriverAgentUrl'],'http://127.0.0.1:19001')
 def test_malformed_setup_state_never_crashes_device_overview(self):
  (self.runtime/'setup-state.json').write_text('[]');self.assertIsNone(dm.setup_status(self.device['udid']))
