import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,Halt
class Foreground(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();cfg=json.loads(Path('src/default-config.json').read_text());cfg['runtime_dir']=self.tmp.name
  self.r=Runner(cfg,notify=lambda _:None);self.r.wait=Mock();self.r.observe=Mock();self.r.command=Mock(return_value='<XCUIElementTypeApplication bundleId="com.burbn.instagram"/>')
 def tearDown(self):self.tmp.cleanup()
 def test_transient_foreground_missing_only_retries_reads(self):
  self.r.mobile=Mock(side_effect=[{}, {'bundleId':'com.burbn.instagram'}]);self.r.snapshot();self.assertEqual(self.r.mobile.call_count,2)
  self.assertTrue(all(c.args==('activeAppInfo',) for c in self.r.mobile.call_args_list));self.r.command.assert_called_once_with('GET','/source')
 def test_persistent_other_app_is_bounded_and_does_not_read_or_activate_it(self):
  self.r.mobile=Mock(return_value={'bundleId':'com.apple.Preferences'})
  with self.assertRaises(NavigationLost):self.r.snapshot()
  self.assertEqual(self.r.mobile.call_count,3);self.r.command.assert_not_called();self.r.observe.assert_not_called();self.assertIsNone(self.r.last_tree)
 def test_changed_source_application_is_not_accepted(self):
  self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'});self.r.command.side_effect=['<XCUIElementTypeApplication bundleId="com.apple.Preferences"/>','<XCUIElementTypeApplication bundleId="com.burbn.instagram"/>']
  tree=self.r.snapshot();self.assertEqual(tree.get('bundleId'),'com.burbn.instagram');self.r.observe.assert_called_once()
 def test_recheck_remains_stoppable(self):
  self.r.mobile=Mock(return_value={});self.r.wait.side_effect=lambda _:self.r.stop.set()
  with self.assertRaises(Halt):self.r.snapshot()
  self.assertEqual(self.r.mobile.call_count,1)
