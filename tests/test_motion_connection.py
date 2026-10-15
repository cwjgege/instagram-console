import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,TransportLost,NavigationLost,Halt,validate_motion,transport_error
CFG=json.loads(Path('src/default-config.json').read_text())

class MotionConnection(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.events=[]
  self.r=Runner({**CFG,'runtime_dir':str(Path(self.tmp.name).resolve())},notify=self.events.append)
  self.r.path='/session/test';self.r.wait=Mock()
 def tearDown(self):self.tmp.cleanup()
 def test_strokes_stay_within_safe_zone_in_both_directions(self):
  actions=[]
  def cmd(method,path,data=None):
   if path=='/window/rect':return dict(width=414,height=896)
   actions.append(data['actions'][0]['actions'])
  self.r.command=cmd
  for direction in ['up','down']*100:
   self.r.gesture(direction);a=actions[-1];start,end=a[0],a[3]
   for point in [start,end]:
    self.assertTrue(123<=point['x']<=241);self.assertTrue(250<=point['y']<=592)
   self.assertEqual(start['x'],end['x']);self.assertEqual(end['y']>start['y'],direction=='down')
   self.assertTrue(196<=abs(end['y']-start['y'])<=288)
   self.assertTrue(220<=end['duration']<=480)
  self.assertGreater(len({a[0]['x'] for a in actions}),1)
 def test_reject_unsafe_configuration(self):
  for change in [dict(gesture_right=90),dict(gesture_bottom=95),dict(gesture_distance_max=40),dict(gesture_duration_min=900)]:
   with self.assertRaises(ValueError):validate_motion({**self.r.cfg,**change})
 def test_post_gesture_verification(self):
  self.r.read_reel=Mock(side_effect=[{'key':'a'},{'key':'b'}]);self.r.gesture=Mock()
  self.r.swipe('up');self.assertEqual(self.r.read_reel.call_count,2)
 def test_unchanged_post_triggers_refresh(self):
  self.r.read_reel=Mock(return_value={'key':'same'});self.r.gesture=Mock()
  self.r.swipe('up');self.assertFalse(self.r.refresh_required)
  self.r.swipe('up')
  with self.assertRaises(NavigationLost):self.r.swipe('up')
  self.assertTrue(self.r.refresh_required)
 def test_delayed_tree_does_not_trigger_recovery(self):
  self.r.read_reel=Mock(side_effect=[{'key':'a'},{'key':'a'},{'key':'b'}]);self.r.gesture=Mock()
  self.r.swipe('up');self.assertEqual(self.r.unchanged_swipes,0)
  self.assertFalse(self.r.refresh_required)
 def test_not_reels_never_gestures(self):
  self.r.read_reel=Mock(side_effect=NavigationLost('not reels'));self.r.gesture=Mock()
  with self.assertRaises(NavigationLost):self.r.swipe('up')
  self.r.gesture.assert_not_called()
 def test_read_retries_once_then_reconnect_signal(self):
  with patch('runner.call',side_effect=RuntimeError('socket hang up')) as call:
   with self.assertRaises(TransportLost):self.r.command('GET','/source')
   self.assertEqual(call.call_count,2)
 def test_click_is_never_replayed(self):
  with patch('runner.call',side_effect=RuntimeError('socket hang up')) as call:
   with self.assertRaises(RuntimeError):self.r.command('POST','/element/x/click',{})
   self.assertEqual(call.call_count,1)
 def test_failed_gesture_reconnects_without_replay(self):
  with patch('runner.call',side_effect=RuntimeError('socket hang up')) as call:
   with self.assertRaises(TransportLost):self.r.command('POST','/actions',{'actions':[]})
   self.assertEqual(call.call_count,1)
 def test_navigation_click_failure_reconnects_without_replay(self):
  self.r.find=Mock(return_value='x')
  with patch('runner.call',side_effect=RuntimeError('socket hang up')) as call:
   with self.assertRaises(TransportLost):self.r.click('reels-tab')
   self.assertEqual(call.call_count,1)
 def test_nontransport_failure_is_not_retried(self):
  with patch('runner.call',side_effect=RuntimeError('invalid selector')) as call:
   with self.assertRaises(RuntimeError):self.r.command('POST','/elements',{})
   self.assertEqual(call.call_count,1)
 def test_usb_reappearance_waits_without_switching_device(self):
  with patch('device_manager.scan_devices',side_effect=[[],[{'udid':'other'}],[{'udid':self.r.cfg['device_udid']}]]) as scan:
   self.r.wait_for_usb();self.assertEqual(scan.call_count,3)
  self.assertEqual([e['event'] for e in self.events],['device_wait','device_ready'])
 def test_usb_timeout_is_clear_and_bounded(self):
  with patch('device_manager.scan_devices',return_value=[]),patch('runner.time.monotonic',side_effect=[0,61]):
   with self.assertRaisesRegex(RuntimeError,'USB.*超时'):self.r.wait_for_usb()
 def test_usb_wait_can_be_stopped(self):
  self.r.stop.set()
  with patch('device_manager.scan_devices') as scan:
   with self.assertRaises(Halt):self.r.wait_for_usb()
   scan.assert_not_called()
 def test_unknown_device_during_session_creation_is_reconnectable(self):
  self.assertTrue(transport_error(RuntimeError('Unknown device or simulator UDID: selected')))
 def test_usb_wait_limit_validation(self):
  with self.assertRaises(ValueError):validate_motion({**self.r.cfg,'usb_wait_seconds':999})
 def test_reconnect_respects_draft_guard(self):
  self.r.connect=Mock();self.r.recover_reels=Mock(side_effect=RuntimeError('输入框'))
  with self.assertRaisesRegex(RuntimeError,'输入框'):self.r.recover_connection()
  self.r.connect.assert_called_once_with(force_new=True)
 def test_interaction_never_reconnects(self):
  self.r.in_interaction=True;self.r.connect=Mock()
  with self.assertRaisesRegex(RuntimeError,'不会重发'):self.r.recover_connection()
  self.r.connect.assert_not_called()
 def test_reconnect_budget_and_stop(self):
  self.r.connect=Mock(side_effect=TransportLost('gone'))
  with self.assertRaisesRegex(RuntimeError,'次数已用完'):self.r.recover_connection()
  self.assertEqual(self.r.connect.call_count,self.r.cfg['reconnect_limit'])
  self.r.stop.set()
  with self.assertRaises(Halt):self.r.recover_connection()
 def test_reconnected_page_is_verified(self):
  self.r.connect=Mock();self.r.recover_reels=Mock()
  self.r.recover_connection();self.r.recover_reels.assert_called_once()
  self.assertEqual(self.r.reconnections,1)

if __name__=='__main__':unittest.main()
