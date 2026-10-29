import json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path('src').resolve()))
import wireless_setup as ws,device_manager as dm
ONE='00008030-0011223344556677'
class WirelessSetup(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.data=patch.object(dm,'DATA',Path(self.tmp.name));self.data.start()
  self.lock=patch('os.environ',new={**__import__('os').environ,'IG_CONSOLE_LOCK_DIR':str(Path(self.tmp.name)/'locks')});self.lock.start()
 def tearDown(self):self.lock.stop();self.data.stop();self.tmp.cleanup()
 def test_prepare_enables_only_selected_usb_phone_without_switching_mode(self):
  with patch.object(ws,'wired',return_value=True),patch.object(ws,'helper') as helper,patch.object(ws.cn,'save') as save:
   self.assertEqual(ws.prepare(ONE)['status'],'enabled');helper.assert_called_once_with(ONE,'--prepare-wireless');save.assert_not_called()
 def test_prepare_requires_usb(self):
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper') as helper,self.assertRaisesRegex(RuntimeError,'USB'):ws.prepare(ONE)
  helper.assert_not_called();self.assertEqual(ws.state(ONE)['status'],'failed')
 def test_both_channels_must_pass_before_switch(self):
  info={'fingerprint':'phone-one','bundle':'com.igconsole.wda.example','ios':'27.0.1'}
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper') as helper,patch.object(ws.cn,'load',return_value={'mode':'usb'}),patch.object(ws.cn,'ensure_wifi',return_value='http://192.168.1.2:8100'),patch.object(ws.cn,'inspect',return_value=info),patch.object(dm,'signing_record',return_value={'bundle_id':'com.igconsole.wda.example.xctrunner'}),patch.object(ws.cn,'save') as save:
   self.assertEqual(ws.verify(ONE)['status'],'verified');helper.assert_called_once_with(ONE,'--check-wireless');self.assertEqual(save.call_args.args[1]['fingerprint'],'phone-one')
 def test_usb_still_attached_cannot_be_called_a_wifi_test(self):
  with patch.object(ws,'wired',return_value=True),patch.object(ws,'helper') as helper,patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'拔掉 USB'):ws.verify(ONE)
  helper.assert_not_called();save.assert_not_called()
 def test_failed_location_check_preserves_original_connection(self):
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper',side_effect=RuntimeError('location unavailable')),patch.object(ws.cn,'save') as save,self.assertRaises(RuntimeError):ws.verify(ONE)
  save.assert_not_called()
 def test_network_discovery_delay_retries_before_switching(self):
  info={'fingerprint':'phone-one','bundle':'com.igconsole.wda.example','ios':'27.0.1'}
  missing=RuntimeError('DeviceNotFoundError: Device not found: usbmux has no device matching udid over Network')
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper',side_effect=[missing,None]) as helper,patch.object(ws.cn,'load',return_value={'mode':'usb'}),patch.object(ws.cn,'ensure_wifi',return_value='http://192.168.1.2:8100'),patch.object(ws.cn,'inspect',return_value=info),patch.object(dm,'signing_record',return_value={'bundle_id':'com.igconsole.wda.example.xctrunner'}),patch.object(ws.cn,'save') as save,patch('threading.Event.wait',return_value=False):
   self.assertEqual(ws.verify(ONE)['status'],'verified');self.assertEqual(helper.call_count,2);save.assert_called_once()
 def test_network_discovery_timeout_explains_and_preserves_connection(self):
  missing=RuntimeError('DeviceNotFoundError: Device not found: usbmux has no device matching udid over Network')
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper',side_effect=missing),patch.object(ws.time,'monotonic',side_effect=[0,31]),patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'未在无线网络发现'):
   ws.verify(ONE)
  save.assert_not_called();self.assertIn('无需重签名',ws.state(ONE)['message'])
 def test_cancellation_never_switches_mode(self):
  stop=threading.Event();stop.set()
  with patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'取消'):ws.verify(ONE,stop)
  save.assert_not_called()
 def test_wrong_helper_identity_rejected(self):
  result=Mock(returncode=0,stdout=json.dumps({'event':'wireless_checked','udid':'00008030-008899AABBCCDDEE','transport':'Network'}))
  with patch.object(ws,'validate_target'),patch.object(ws,'worker_command',return_value=['helper']),patch.object(ws.subprocess,'run',return_value=result),self.assertRaisesRegex(RuntimeError,'其他设备'):ws.helper(ONE,'--check-wireless')
 def test_wrong_controller_never_saved(self):
  info={'fingerprint':'phone-one','bundle':'com.other.runner','ios':'27.0.1'}
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper'),patch.object(ws.cn,'load',return_value={'mode':'usb'}),patch.object(ws.cn,'ensure_wifi',return_value='http://192.168.1.2:8100'),patch.object(ws.cn,'inspect',return_value=info),patch.object(dm,'signing_record',return_value={'bundle_id':'com.igconsole.wda.example.xctrunner'}),patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'不匹配'):ws.verify(ONE)
  save.assert_not_called()
 def test_usb_reinserted_during_validation_cannot_save_wifi(self):
  info={'fingerprint':'phone-one','bundle':'com.igconsole.wda.example','ios':'27.0.1'}
  with patch.object(ws,'wired',side_effect=[False,False,True]),patch.object(ws,'helper'),patch.object(ws.cn,'load',return_value={'mode':'usb'}),patch.object(ws.cn,'ensure_wifi',return_value='http://192.168.1.2:8100'),patch.object(ws.cn,'inspect',return_value=info),patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'拔掉 USB'):ws.verify(ONE)
  save.assert_not_called()
 def test_cancelled_during_control_check_cannot_save_wifi(self):
  stop=threading.Event();info={'fingerprint':'phone-one','bundle':'com.igconsole.wda.example','ios':'27.0.1'}
  def ready(*args,**kwargs):stop.set();return 'http://192.168.1.2:8100'
  with patch.object(ws,'wired',return_value=False),patch.object(ws,'helper'),patch.object(ws.cn,'load',return_value={'mode':'usb'}),patch.object(ws.cn,'ensure_wifi',side_effect=ready),patch.object(ws.cn,'inspect',return_value=info),patch.object(ws.cn,'save') as save,self.assertRaisesRegex(RuntimeError,'取消'):ws.verify(ONE,stop)
  save.assert_not_called()
