import sys,json,tempfile,unittest,copy
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from page_state import deferred_apple_account_alert,PageBlocked
from runner import Runner,ELEMENT
CFG=json.loads(Path('src/default-config.json').read_text())
def fixture():return ET.parse('tests/fixtures/apple-account-reminder.xml').getroot()
class SystemReminder(unittest.TestCase):
 def test_observed_system_reminder_matches(self):self.assertEqual(deferred_apple_account_alert(fixture()).get('label'),'Not Now')
 def test_unknown_titles_apps_fields_and_buttons_never_match(self):
  for change in ('app','title','secure','keyboard','button','body','duplicate'):
   t=fixture();a=t.find('.//XCUIElementTypeAlert')
   if change=='app':t.set('bundleId','com.burbn.instagram')
   elif change=='title':a.set('label','Verify your identity')
   elif change in ('secure','keyboard'):ET.SubElement(a,'XCUIElementTypeSecureTextField' if change=='secure' else 'XCUIElementTypeKeyboard',visible='true')
   elif change=='button':next(e for e in a.iter() if e.get('label')=='Settings').set('label','Continue')
   elif change=='body':
    for e in a.iter():
     if e.get('label','').startswith('Enter the password for '):e.set('label','Confirm your identity now')
   else:a.append(copy.deepcopy(next(e for e in a.iter() if e.get('label')=='Not Now')))
   with self.subTest(change=change):self.assertIsNone(deferred_apple_account_alert(t))
 def runner(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  r=Runner({**CFG,'runtime_dir':self.tmp.name},notify=lambda _:None);r.wait=Mock();r.path='/session/test';return r
 def test_snapshot_defers_once_then_rechecks_instagram(self):
  r=self.runner();instagram=ET.fromstring('<XCUIElementTypeApplication bundleId="com.burbn.instagram"/>')
  r.mobile=Mock(side_effect=[{'bundleId':'com.apple.springboard'},{'bundleId':'com.burbn.instagram'}])
  r.command=Mock(side_effect=[ET.tostring(fixture(),encoding='unicode'),[{ELEMENT:'not-now'}],{},ET.tostring(instagram,encoding='unicode')])
  self.assertEqual(r.snapshot().get('bundleId'),'com.burbn.instagram')
  self.assertEqual([c.args for c in r.command.call_args_list if c.args[1].endswith('/click')],[('POST','/element/not-now/click',{})])
  self.assertTrue(r.deferred_account_reminder)
 def test_repeated_reminder_blocks_without_second_click(self):
  r=self.runner();r.deferred_account_reminder=True;r.mobile=Mock(return_value={'bundleId':'com.apple.springboard'})
  r.command=Mock(return_value=ET.tostring(fixture(),encoding='unicode'))
  with self.assertRaises(PageBlocked):r.snapshot()
  self.assertEqual(r.command.call_count,1)
 def test_non_unique_button_does_not_click(self):
  r=self.runner();r.mobile=Mock(return_value={'bundleId':'com.apple.springboard'})
  r.command=Mock(side_effect=[ET.tostring(fixture(),encoding='unicode'),[{ELEMENT:'a'},{ELEMENT:'b'}]])
  with self.assertRaises(PageBlocked):r.snapshot()
  self.assertEqual(r.command.call_count,2)
