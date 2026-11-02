"""Bound XCTest recovery without repeating signing, gestures, or other devices."""
import json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
import connections as cn,controller_bridge as bridge,device_manager as dm
from setup_errors import SetupError,classify_setup_error
from runner import Halt
from runtime_manager import ExistingProcess

UDID='a'*40
FAILURE='Connection peer refused channel request for XCTestDriverInterface\nExiting due to IDE disconnection.\nThe test runner exited with code 74 before establishing connection.'

class StartupRecovery(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.scope=patch.object(dm,'DATA',Path(self.tmp.name));self.scope.start();self.addCleanup(self.scope.stop)

 def test_retries_once_after_cleanup_then_records_success_without_signing(self):
  order=[]
  def connect(udid,notify,stop,setting,trace):
   order.append('connect')
   if order.count('connect')==1:raise classify_setup_error(RuntimeError('startup'),FAILURE)
   self.assertEqual(trace['recovery_attempt'],1)
   return 'http://127.0.0.1:19001'
  with patch.object(cn,'_ensure_wifi',side_effect=connect),patch.object(cn,'stop_device',side_effect=lambda udid:order.append('cleanup')),patch.object(cn.time,'sleep'),patch.object(dm,'provision') as sign:
   self.assertEqual(cn.ensure_wifi(UDID),'http://127.0.0.1:19001')
  self.assertEqual(order,['connect','cleanup','connect']);sign.assert_not_called()
  evidence=json.loads((dm.device_dir(UDID)/'connection-attempt.json').read_text())
  self.assertEqual(evidence['previous_error_code'],'automation_startup')

 def test_persistent_failure_stops_after_two_attempts_with_actionable_diagnostics(self):
  with patch.object(cn,'_ensure_wifi',side_effect=classify_setup_error(RuntimeError('startup'),FAILURE)) as connect,patch.object(cn,'stop_device') as stop,patch.object(cn.time,'sleep'),patch.object(bridge,'ensure') as fallback:
   with self.assertRaisesRegex(SetupError,'自动重试一次后仍未恢复'):cn.ensure_wifi(UDID)
  self.assertEqual(connect.call_count,2);self.assertEqual(stop.call_args_list[0].args,(UDID,));fallback.assert_not_called()
  evidence=json.loads((dm.device_dir(UDID)/'connection-diagnostic.json').read_text())
  self.assertEqual(evidence['error_code'],'automation_startup');self.assertEqual(evidence['recovery_attempt'],1)

 def test_signature_identity_and_generic_transport_failures_are_not_retried(self):
  for error in (SetupError('signature_invalid','expired'),cn.FingerprintMismatch('wrong phone'),RuntimeError('connection refused')):
   with self.subTest(error=error),patch.object(cn,'_ensure_wifi',side_effect=error) as connect,patch.object(cn,'stop_device'),patch.object(cn.time,'sleep') as wait:
    with self.assertRaises(RuntimeError):cn.ensure_wifi(UDID)
    self.assertEqual(connect.call_count,1);wait.assert_not_called()

 def test_stop_during_retry_wait_cancels_without_second_start(self):
  stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
  with patch.object(cn,'_ensure_wifi',side_effect=classify_setup_error(RuntimeError('startup'),FAILURE)) as connect,patch.object(cn,'stop_device'):
   with self.assertRaises(Halt):cn.ensure_wifi(UDID,stop=stop)
  self.assertEqual(connect.call_count,1)

 def test_already_stopped_never_starts_or_cleans_another_task(self):
  stop=threading.Event();stop.set()
  with patch.object(cn,'_ensure_wifi_attempt') as connect,patch.object(cn,'stop_device') as cleanup:
   with self.assertRaises(Halt):cn.ensure_wifi(UDID,stop=stop)
  connect.assert_not_called();cleanup.assert_not_called()

 def test_bridge_reads_native_error_before_generic_json_exit_summary(self):
  path=Path(self.tmp.name)/'controller.log'
  path.write_text(FAILURE+'\n'+json.dumps({'event':'error','error':'标准 Xcode 启动提前结束，退出码 65'}))
  error=bridge.startup_failure(path,1)
  self.assertIsInstance(error,SetupError);self.assertEqual(error.code,'automation_startup')

 def test_adopted_process_supports_bounded_cleanup_wait(self):
  process=ExistingProcess(12345)
  with patch.object(process,'poll',side_effect=[None,0]),patch('runtime_manager.time.sleep'):
   self.assertEqual(process.wait(timeout=5),0)
  import subprocess
  with patch.object(process,'poll',return_value=None):
   with self.assertRaises(subprocess.TimeoutExpired):process.wait(timeout=0)

 def test_identity_mismatch_still_blocks_recovery_with_native_startup_errors(self):
  path=Path(self.tmp.name)/'controller.log'
  path.write_text(FAILURE+'\n'+json.dumps({'event':'error','error':'IdentityMismatch: wrong device'}))
  self.assertIsInstance(bridge.startup_failure(path,1),cn.FingerprintMismatch)

class StartupSecurityRecovery(unittest.TestCase):
 setUp=StartupRecovery.setUp
 def test_security_rejection_recovers_once_without_signing_or_changing_other_devices(self):
  for code in ('developer_untrusted','signature_or_trust'):
   with self.subTest(code=code):
    notifications=[];other=Mock();cn._PROCESSES['b'*40]=other
    try:
     with patch.object(cn,'_ensure_wifi',side_effect=[SetupError(code,'ambiguous startup rejection'),'http://127.0.0.1:19001']) as connect,patch.object(cn,'stop_device') as cleanup,patch.object(cn.time,'sleep'),patch.object(dm,'provision') as sign:
      self.assertEqual(cn.ensure_wifi(UDID,notifications.append),'http://127.0.0.1:19001')
     self.assertEqual(connect.call_count,2);cleanup.assert_called_once_with(UDID);sign.assert_not_called();other.terminate.assert_not_called()
     self.assertTrue(any('尚不能断定证书失效' in n for n in notifications));self.assertTrue(any('已自动恢复' in n for n in notifications))
     evidence=json.loads((dm.device_dir(UDID)/'connection-attempt.json').read_text())
     self.assertTrue(evidence['recovery_succeeded']);self.assertEqual(evidence['previous_error_code'],code)
    finally:cn._PROCESSES.pop('b'*40,None)
 def test_persistent_security_rejection_is_neutral_and_actionable(self):
  for code in ('developer_untrusted','signature_or_trust'):
   with self.subTest(code=code),patch.object(cn,'_ensure_wifi',side_effect=SetupError(code,'refused')) as connect,patch.object(cn,'stop_device'),patch.object(cn.time,'sleep'):
    with self.assertRaises(SetupError) as caught:cn.ensure_wifi(UDID)
    self.assertEqual(connect.call_count,2);self.assertEqual(caught.exception.code,code)
    for text in ('自动重试一次后仍未恢复','不足以确认','USB','重新验证已安装组件','VPN 与设备管理'):self.assertIn(text,str(caught.exception))
 def test_explicit_revocation_wins_over_ambiguous_trust_and_never_retries(self):
  issue=classify_setup_error(RuntimeError('startup'),'Developer App Certificate is not trusted; certificate was revoked')
  self.assertEqual(issue.code,'signature_invalid')
  with patch.object(cn,'_ensure_wifi',side_effect=issue) as connect,patch.object(cn,'stop_device'),patch.object(cn.time,'sleep') as wait:
   with self.assertRaises(SetupError):cn.ensure_wifi(UDID)
   connect.assert_called_once();wait.assert_not_called()
 def test_other_known_failures_are_not_auto_retried(self):
  for code in ('locked','developer_mode','developer_agreement_required','signing_failed','verification'):
   with self.subTest(code=code),patch.object(cn,'_ensure_wifi',side_effect=SetupError(code,'blocked')) as connect,patch.object(cn,'stop_device'),patch.object(cn.time,'sleep') as wait:
    with self.assertRaises(SetupError):cn.ensure_wifi(UDID)
    connect.assert_called_once();wait.assert_not_called()
 def test_security_retry_is_cancelable(self):
  stop=Mock();stop.is_set.return_value=False;stop.wait.return_value=True
  with patch.object(cn,'_ensure_wifi',side_effect=SetupError('developer_untrusted','refused')) as connect,patch.object(cn,'stop_device'):
   with self.assertRaises(Halt):cn.ensure_wifi(UDID,stop=stop)
   connect.assert_called_once()
 def test_bridge_preserves_explicit_security_and_revocation_errors(self):
  path=Path(self.tmp.name)/'controller.log'
  for message,code in (('Developer App Certificate is not trusted','developer_untrusted'),('invalid code signature','signature_or_trust'),('certificate was revoked','signature_invalid')):
   path.write_text(message+'\n'+json.dumps({'event':'error','error':'标准 Xcode 启动提前结束，退出码 65'}))
   self.assertEqual(bridge.startup_failure(path,1).code,code)
