import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path('src').resolve()))

class NetworkVerification(unittest.TestCase):
 def test_explicit_online_verification_takes_precedence_over_generic_xcode_trust_message(self):
  from setup_errors import classify_setup_error
  issue=classify_setup_error(RuntimeError('Developer App Certificate is not trusted'), 'Profile Needs Network Validation: Unable to Verify App')
  self.assertEqual(issue.code,'network_validation');self.assertIn('ppq.apple.com',str(issue))
 def test_generic_trust_message_does_not_assume_a_manual_trust_entry_exists(self):
  from setup_errors import classify_setup_error
  issue=classify_setup_error(RuntimeError('Developer App Certificate is not trusted'))
  self.assertIn('若没有条目',str(issue))

 def test_missing_xcode_team_account_is_distinct_from_phone_trust(self):
  from setup_errors import classify_setup_error
  issue=classify_setup_error(RuntimeError('xcodebuild failed code 65'), 'No Account for Team "EXAMPLETEAM". No profiles for bundle were found.')
  self.assertEqual(issue.code,'signing_account_missing');self.assertIn('钥匙串中有旧证书',str(issue))

class AutomationStartup(unittest.TestCase):
 def test_runner_exit_is_not_reported_as_usb_trust_or_transport_loss(self):
  from setup_errors import classify_setup_error
  from runner import transport_error
  logs='Connection was refused to port 8100\nConnection peer refused channel request for "dtxproxy:XCTestDriverInterface:XCTestManager_IDEInterface"\nExiting due to IDE disconnection.\nxcodebuild failed with code 65'
  issue=classify_setup_error(RuntimeError('socket hang up'),logs)
  self.assertEqual(issue.code,'automation_startup')
  self.assertIn('重启这台 iPhone',str(issue));self.assertIn('不重新签名',str(issue))
  self.assertFalse(transport_error(issue))
 def test_generic_code_65_or_refused_port_is_not_enough_to_infer_automation_failure(self):
  from setup_errors import classify_setup_error
  self.assertEqual(classify_setup_error(RuntimeError('xcodebuild failed with code 65')).code,'unknown')
  issue=classify_setup_error(RuntimeError('connection refused to port 8100'))
  self.assertEqual(issue.code,'connection');self.assertNotIn('「信任此电脑」',str(issue))
 def test_explicit_signature_failure_still_takes_precedence(self):
  from setup_errors import classify_setup_error
  self.assertEqual(classify_setup_error(RuntimeError('Exiting due to IDE disconnection'), 'Provisioning profile has expired').code,'signature_invalid')
 def test_only_new_wireless_logs_are_classified(self):
  import tempfile
  from setup_errors import log_positions,attempt_text,classify_setup_error
  with tempfile.TemporaryDirectory() as tmp:
   log=Path(tmp)/'wireless-wda.log';log.write_text('Exiting due to IDE disconnection.\n')
   positions=log_positions(tmp)
   with log.open('a') as stream:stream.write('connection refused\n')
   self.assertEqual(classify_setup_error(RuntimeError('failure'),attempt_text(tmp,positions)).code,'connection')
