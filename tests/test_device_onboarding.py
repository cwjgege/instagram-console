import sys,queue,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from device_ui import DeviceWindow
import device_manager as dm
class Onboarding(unittest.TestCase):
 def window(self):
  w=DeviceWindow.__new__(DeviceWindow);w.panel=Mock();w.panel.worker=None;w.panel.cfg={};w.window=Mock();w.status=Mock();w.events=queue.Queue();w.busy=False
  w.devices=[];w.device_box=Mock();w.team_box=Mock();w.team=Mock();w.device=Mock();w.environment=Mock();w.team_map={};w.show_device=Mock()
  w.install_button=Mock();w.use_button=Mock();w.progress=Mock();w.selected=Mock(return_value={'udid':'A'*20,'name':'Test iPhone'})
  return w
 def test_missing_signing_record_blocks_selection_and_explains(self):
  w=self.window()
  with patch.object(dm,'signing_record',return_value=None),patch('device_ui.messagebox.showwarning') as warning:
   w.use_device()
   warning.assert_called_once();self.assertIn('没有有效安装记录',w.status.set.call_args.args[0]);w.panel.save_settings.assert_not_called()
 def test_automatic_scan_preserves_last_install_failure(self):
  w=self.window();w.last_result='install failed';w.team.get.return_value='team';w.events.put(('scanned',(Path('/Applications/Xcode.app/Contents/Developer'),[],{})))
  w.poll();w.status.set.assert_not_called();w.window.after.assert_called_once()
 def test_install_failure_remains_visible_after_background_scan(self):
  w=self.window();w.events.put(('error','Xcode build failed'));w.events.put(('done',None));w.poll()
  self.assertEqual(w.last_result,'Xcode build failed');self.assertFalse(w.busy)
 def test_no_device_selection_is_explained(self):
  w=self.window();w.selected.return_value=None;w.use_device();self.assertIn('未选择 USB',w.status.set.call_args.args[0])
 def test_payload_always_captures_xcode_diagnostics(self):
  import tempfile
  with tempfile.TemporaryDirectory() as folder,patch.object(dm,'DATA',Path(folder)):
   caps=dm.make_payload({'udid':'A'*20,'name':'Test'},'ABCDE12345')['capabilities']['alwaysMatch']
  self.assertTrue(caps['appium:showXcodeLog']);self.assertTrue(caps['appium:allowProvisioningDeviceRegistration']);self.assertNotIn('appium:allowProvisioningUpdates',caps)
if __name__=='__main__':unittest.main()
