import sys,time,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
import device_manager as dm,location_manager as lm
from location_guard import LocationGuard
from location_readback import sample,ReadbackError,distance_m,LocationReader
ONE='00008030-0011223344556677';TWO='00008030-008899AABBCCDDEE'
def reading(**changes):
 return {'locationSchema':1,'deviceFingerprint':'phone-one','authorizationStatus':3,'locationServicesEnabled':True,'accuracyAuthorization':0,'valid':True,'latitude':40.7128,'longitude':-74.006,'timestamp':time.time(),'horizontalAccuracy':5,**changes}
class Readback(unittest.TestCase):
 def test_fresh_sample_accepts_equator_and_prime_meridian(self):
  self.assertEqual(sample(reading(latitude=0,longitude=0),'phone-one')['latitude'],0)
 def test_stock_api_cannot_masquerade_as_current_position(self):
  with self.assertRaises(ReadbackError):sample({'latitude':0,'longitude':0,'authorizationStatus':3},'phone-one')
 def test_invalid_stale_future_or_unauthorized_never_passes(self):
  for change in ({'timestamp':time.time()-30},{'timestamp':time.time()+60},{'timestamp':None},{'horizontalAccuracy':-1},{'horizontalAccuracy':500},{'horizontalAccuracy':float('nan')},{'valid':False},{'latitude':None},{'longitude':float('inf')},{'deviceFingerprint':'another-phone'},{'authorizationStatus':4},{'locationServicesEnabled':False},{'accuracyAuthorization':1}):
   with self.subTest(change=change),self.assertRaises(ReadbackError):sample(reading(**change),'phone-one')
 def test_distance_across_dateline(self):
  self.assertLess(distance_m((0,179.9999),(0,-179.9999)),30)
  self.assertGreater(distance_m((40.7128,-74.006),(34.0522,-118.2437)),3000000)
 def test_wifi_sample_requires_saved_identity_and_uses_device_endpoint(self):
  reader=LocationReader()
  with patch('connections.load',return_value={'mode':'remote','wda_url':'http://192.168.1.9:8100','fingerprint':'phone-one'}),patch('connections.checked') as checked,patch('connections.get',return_value=reading()) as get:
   reader.read(ONE);checked.assert_called_with('http://192.168.1.9:8100','phone-one');self.assertEqual(get.call_args.args[1],'/wda/device/location')
 def test_permission_request_only_activates_the_signed_selected_bundle(self):
  reader=LocationReader();record={'udid':ONE,'bundle_id':'com.example.wda.xctrunner'}
  with patch('location_manager.validate_target'),patch.object(dm,'signing_record',return_value=record),patch.object(reader,'prepare',return_value=('http://192.168.1.9:8100','phone-one')),patch.object(dm,'coredevice') as native,patch('connections.checked'),patch('connections.get',return_value=reading(authorizationStatus=0)):reader.request_permission(ONE)
  native.assert_called_once_with(['device','process','launch','--device',ONE,'com.example.wda.xctrunner'])
 def test_wrong_signing_record_cannot_activate_a_phone_app(self):
  with patch('location_manager.validate_target'),patch.object(dm,'signing_record',return_value={'udid':TWO}),patch.object(dm,'coredevice') as native,self.assertRaises(ReadbackError):LocationReader().request_permission(ONE)
  native.assert_not_called()
 def test_wrong_usb_session_does_not_bind_a_device(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(dm,'DATA',Path(tmp)):
   dm.write_json(dm.device_dir(ONE)/'active-session.json',{'capabilities':{'udid':TWO}})
   with patch('connections.load',return_value={'mode':'usb'}),patch('connections.inspect') as inspect,self.assertRaises(ReadbackError):LocationReader().read(ONE)
   inspect.assert_not_called()

class Guard(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.data=patch.object(dm,'DATA',Path(self.tmp.name));self.data.start();self.m=lm.LocationManager();self.readers=[]
  def factory():
   r=Mock();self.readers.append(r);return r
  self.guard=LocationGuard(self.m,factory);self.m.guard=self.guard;self.m._request=Mock()
 def tearDown(self):self.guard.stop_all();self.data.stop();self.tmp.cleanup()
 def start(self,udid=ONE):
  self.m._state(udid,'applied','accepted',latitude=lm.NEW_YORK[0],longitude=lm.NEW_YORK[1])
  s=self.guard.start(udid,lm.NEW_YORK,background=False);s['reader'].read.return_value=sample(reading(),'phone-one');return s
 def test_valid_deviation_triggers_exact_saved_target_and_waits_for_confirmation(self):
  s=self.start();s['reader'].read.return_value=sample(reading(latitude=40.8),'phone-one');self.guard.tick(ONE,s)
  self.m._request.assert_called_once_with(ONE,{'action':'set','udid':ONE,'latitude':40.7128,'longitude':-74.006})
  self.assertEqual(self.m.snapshot(ONE)['guard_status'],'correcting')
  s['reader'].read.return_value=sample(reading(),'phone-one');self.guard.tick(ONE,s)
  self.assertEqual(self.m.snapshot(ONE)['guard_status'],'confirmed')
 def test_jitter_inside_accuracy_uncertainty_does_not_rewrite(self):
  s=self.start();s['reader'].read.return_value=sample(reading(latitude=40.714,horizontalAccuracy=150),'phone-one');self.guard.tick(ONE,s)
  self.m._request.assert_not_called()
 def test_unavailable_read_does_not_blindly_reapply(self):
  s=self.start();s['reader'].read.side_effect=ReadbackError('stale');self.guard.tick(ONE,s)
  self.m._request.assert_not_called();self.assertEqual(self.m.snapshot(ONE)['guard_status'],'unconfirmed')
 def test_restoring_while_a_read_is_in_flight_prevents_late_correction(self):
  s=self.start();entered=threading.Event();release=threading.Event()
  def read(udid):entered.set();release.wait(3);return sample(reading(latitude=41),'phone-one')
  s['reader'].read.side_effect=read;t=threading.Thread(target=self.guard.tick,args=(ONE,s));t.start();self.assertTrue(entered.wait(2))
  with patch('location_manager.validate_target'):self.m.clear(ONE)
  release.set();t.join(3);self.assertFalse(t.is_alive())
  self.assertEqual(self.m._request.call_args_list[0].args,(ONE,{'action':'clear','udid':ONE}));self.assertEqual(self.m._request.call_count,1)
 def test_changing_target_invalidates_old_session(self):
  old=self.start();new=self.guard.start(ONE,(34,-118),background=False)
  old['reader'].read.return_value=sample(reading(latitude=42),'phone-one');self.guard.tick(ONE,old)
  self.m._request.assert_not_called();self.assertIs(self.guard.sessions[ONE],new)
 def test_three_corrections_in_five_minutes_pause_further_writes(self):
  s=self.start();s['reader'].read.return_value=sample(reading(latitude=42),'phone-one')
  for _ in range(4):self.guard.tick(ONE,s)
  self.assertEqual(self.m._request.call_count,3);self.assertEqual(self.m.snapshot(ONE)['guard_status'],'paused')
 def test_busy_request_does_not_read_or_queue_another_set(self):
  s=self.start();self.m._state(ONE,'busy','connecting');self.guard.tick(ONE,s)
  s['reader'].read.assert_not_called();self.m._request.assert_not_called()
 def test_pre_write_sample_is_not_used_to_report_success(self):
  s=self.start();self.m.states[ONE]['last_apply_at']=time.time()+3;self.guard.tick(ONE,s)
  self.assertEqual(self.m.snapshot(ONE)['guard_status'],'waiting');self.m._request.assert_not_called()
 def test_stopping_one_phone_does_not_stop_another_guard(self):
  a=self.start();b=self.start(TWO);self.guard.stop(ONE);self.guard.tick(TWO,b)
  self.assertNotIn(ONE,self.guard.sessions);self.assertEqual(self.m.snapshot(TWO)['guard_status'],'confirmed')
 def test_stationary_sample_waits_without_fabricating_a_fresh_fix(self):
  from location_readback import StaleLocation
  s=self.start();s['reader'].read.side_effect=StaleLocation('waiting for update');self.guard.tick(ONE,s)
  self.m._request.assert_not_called();self.assertEqual(self.m.snapshot(ONE)['guard_status'],'waiting')
 def test_shutdown_stops_guard_before_clearing_owned_location_processes(self):
  s=self.start();self.m.shutdown();self.guard.tick(ONE,s)
  self.assertFalse(self.guard.sessions);self.m._request.assert_not_called()

class ConnectionPreparation(unittest.TestCase):
 def test_expired_session_reconnects_existing_signed_component_without_install(self):
  reader=LocationReader();record={'udid':ONE,'expires_at':'2099-01-01T00:00:00+00:00'}
  with patch.object(reader,'endpoint',side_effect=[ReadbackError('session expired'),('http://127.0.0.1:19001','pin')]),patch.object(dm,'signing_record',return_value=record),patch('connections.load',return_value={'mode':'usb'}),patch.object(dm,'verify_provision') as verify,patch.object(dm,'provision') as install:
   self.assertEqual(reader.prepare(ONE),('http://127.0.0.1:19001','pin'))
   verify.assert_called_once();install.assert_not_called()
 def test_identity_mismatch_never_reconnects_or_installs(self):
  from location_readback import IdentityMismatch
  with patch.object(LocationReader,'endpoint',side_effect=IdentityMismatch('wrong phone')),patch.object(dm,'verify_provision') as verify,self.assertRaises(IdentityMismatch):LocationReader().prepare(ONE)
  verify.assert_not_called()
 def test_cancelled_preparation_does_not_reconnect(self):
  stop=threading.Event();stop.set()
  with patch.object(dm,'verify_provision') as verify,self.assertRaises(ReadbackError):LocationReader().prepare(ONE,stop)
  verify.assert_not_called()
 def test_ready_permission_does_not_reactivate_the_phone_app(self):
  reader=LocationReader()
  with patch('location_manager.validate_target'),patch.object(dm,'signing_record',return_value={'udid':ONE,'bundle_id':'app'}),patch.object(reader,'prepare',return_value=('http://127.0.0.1:19001','pin')),patch('connections.get',return_value=reading()),patch.object(dm,'coredevice') as native:
   reader.request_permission(ONE);native.assert_not_called()
