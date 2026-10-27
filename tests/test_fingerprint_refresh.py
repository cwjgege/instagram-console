import json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path('src').resolve()))
import connections as cn,device_manager as dm
from runner import Halt
UDID='00008030-0011223344556677';OTHER='00008030-008899AABBCCDDEE';URL='http://192.168.1.2:8100'
class IdentityRefresh(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.scope=patch.object(dm,'DATA',Path(self.tmp.name));self.scope.start()
  self.old={'mode':'wifi','identifier_kind':'udid','fingerprint':'old','sentinel':'keep task settings'};cn.save(UDID,self.old)
  self.setting=dict(self.old);self.info={'fingerprint':'new','bundle':'com.igconsole.wda.example','ios':'27.0.1'}
  self.proof={**self.info,'udid':UDID,'port':8100};self.record={'udid':UDID,'bundle_id':'com.igconsole.wda.example.xctrunner'}
 def tearDown(self):self.scope.stop();self.tmp.cleanup()
 def refresh(self,proof=None,record=None,stop=None,checked=None):
  with patch.object(dm,'signing_record',return_value=self.record if record is None else record),patch.object(dm,'expiry_state',return_value=('valid',24)),patch.object(dm,'controller_identity',return_value=self.proof if proof is None else proof),patch.object(cn,'checked',return_value=self.info if checked is None else checked):
   cn.refresh_wifi_identity(UDID,self.setting,URL,self.info,8100,stop)
 def test_matching_paired_device_and_local_signature_updates_only_binding(self):
  self.refresh();saved=cn.load(UDID)
  self.assertEqual(saved['fingerprint'],'new');self.assertEqual(saved['sentinel'],'keep task settings');self.assertEqual(self.setting['fingerprint'],'new')
  audit=json.loads((dm.device_dir(UDID)/'fingerprint-refresh.json').read_text());self.assertEqual(audit['previous_fingerprint'],'old')
 def test_other_phone_at_same_ip_never_updates_binding(self):
  with self.assertRaises(cn.FingerprintMismatch):self.refresh(proof={**self.proof,'udid':OTHER})
  self.assertEqual(cn.load(UDID),self.old)
 def test_other_fingerprint_on_paired_channel_never_updates_binding(self):
  with self.assertRaises(cn.FingerprintMismatch):self.refresh(proof={**self.proof,'fingerprint':'another'})
  self.assertEqual(cn.load(UDID),self.old)
 def test_other_signing_bundle_never_updates_binding(self):
  with self.assertRaises(cn.FingerprintMismatch):self.refresh(proof={**self.proof,'bundle':'com.other'})
  self.assertEqual(cn.load(UDID),self.old)
 def test_another_devices_install_record_cannot_authorize_refresh(self):
  with self.assertRaises(cn.FingerprintMismatch):self.refresh(record={**self.record,'udid':OTHER})
  self.assertEqual(cn.load(UDID),self.old)
 def test_remote_only_profile_never_auto_refreshes(self):
  self.setting['mode']='remote'
  with self.assertRaises(cn.FingerprintMismatch):self.refresh()
  self.assertEqual(cn.load(UDID),self.old)
 def test_expired_signature_never_reads_or_updates(self):
  with patch.object(dm,'signing_record',return_value=self.record),patch.object(dm,'expiry_state',return_value=('expired',-1)),patch.object(dm,'controller_identity') as probe,self.assertRaises(cn.FingerprintMismatch):cn.refresh_wifi_identity(UDID,self.setting,URL,self.info,8100)
  probe.assert_not_called();self.assertEqual(cn.load(UDID),self.old)
 def test_cancel_before_proof_preserves_binding(self):
  stop=threading.Event();stop.set()
  with self.assertRaises(Halt):self.refresh(stop=stop)
  self.assertEqual(cn.load(UDID),self.old)
 def test_service_replaced_during_proof_never_updates(self):
  with self.assertRaises(cn.FingerprintMismatch):self.refresh(checked={**self.info,'bundle':'com.other'})
  self.assertEqual(cn.load(UDID),self.old)
 def test_concurrent_transport_change_is_not_overwritten(self):
  cn.save(UDID,{'mode':'usb'})
  with self.assertRaises(cn.FingerprintMismatch):self.refresh()
  self.assertEqual(cn.load(UDID),{'mode':'usb'})
 def test_duplicate_other_phone_binding_still_blocks_refresh(self):
  cn.save(OTHER,{'mode':'wifi','fingerprint':'new'})
  with self.assertRaisesRegex(RuntimeError,'已经绑定'):self.refresh()
  self.assertEqual(cn.load(UDID),self.old)
 def test_compare_and_swap_checks_latest_binding_under_write_lock(self):
  with self.assertRaisesRegex(RuntimeError,'其他操作'):cn.save(UDID,{**self.old,'fingerprint':'new'},expected_fingerprint='obsolete')
  self.assertEqual(cn.load(UDID),self.old)
 def details(self):return {'hardwareProperties':{'udid':UDID},'connectionProperties':{'pairingState':'paired','tunnelIPAddress':'fd00::1'},'deviceProperties':{'developerModeStatus':'enabled','osVersionNumber':'27.0.1'}}
 def test_startup_recovers_changed_cached_identity_without_usb_or_reinstall(self):
  info={**self.info,'url':URL};dm.write_json(dm.device_dir(UDID)/'network-runtime.json',{'url':URL,'fingerprint':'old'})
  with patch.object(dm,'signing_record',return_value=self.record),patch.object(dm,'expiry_state',return_value=('valid',24)),patch.object(dm,'coredevice',return_value=self.details()),patch.object(dm,'controller_identity',return_value=self.proof) as probe,patch.object(cn,'inspect',return_value=info),patch.object(cn.subprocess,'Popen') as launch:
   self.assertEqual(cn.ensure_wifi(UDID),URL)
  probe.assert_called_once_with(UDID,8100);launch.assert_not_called();self.assertEqual(cn.load(UDID)['fingerprint'],'new')
 def test_wrong_cached_phone_cannot_authorize_rebind_but_paired_tunnel_can(self):
  tunnel='http://[fd00::1]:8100';dm.write_json(dm.device_dir(UDID)/'network-runtime.json',{'url':URL})
  def inspect(url):return {**self.info,'url':url,'fingerprint':'another-phone' if url==URL else 'new'}
  with patch.object(dm,'signing_record',return_value=self.record),patch.object(dm,'expiry_state',return_value=('valid',24)),patch.object(dm,'coredevice',return_value=self.details()),patch.object(dm,'controller_identity',return_value=self.proof),patch.object(cn,'inspect',side_effect=inspect):self.assertEqual(cn.ensure_wifi(UDID),tunnel)
  self.assertEqual(cn.load(UDID)['fingerprint'],'new')
 def test_cancel_after_paired_proof_never_saves(self):
  stop=threading.Event()
  def probe(*args):stop.set();return self.proof
  with patch.object(dm,'signing_record',return_value=self.record),patch.object(dm,'expiry_state',return_value=('valid',24)),patch.object(dm,'controller_identity',side_effect=probe),self.assertRaises(Halt):cn.refresh_wifi_identity(UDID,self.setting,URL,self.info,8100,stop)
  self.assertEqual(cn.load(UDID),self.old)
 def test_location_reader_refreshes_changed_local_wifi_identity(self):
  from location_readback import LocationReader
  reader=LocationReader();dm.write_json(dm.device_dir(UDID)/'network-runtime.json',{'url':URL})
  with patch.object(cn,'load',side_effect=[self.old,{**self.old,'fingerprint':'new'}]),patch.object(cn,'checked',side_effect=[cn.FingerprintMismatch('changed'),self.info]),patch.object(cn,'ensure_wifi',return_value=URL) as ensure,patch.object(reader,'session',return_value=None):self.assertEqual(reader.endpoint(UDID),(URL,'new'))
  ensure.assert_called_once_with(UDID)
 def test_same_pin_but_concurrent_configuration_edit_is_preserved(self):
  current={**self.old,'sentinel':'new user setting'};cn.save(UDID,current)
  with self.assertRaisesRegex(RuntimeError,'其他操作'):cn.save(UDID,{**self.old,'fingerprint':'new'},expected_fingerprint='old',expected_setting=self.old)
  self.assertEqual(cn.load(UDID),current)
