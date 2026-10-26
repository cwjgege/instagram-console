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
