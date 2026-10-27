import copy,json,queue,shutil,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path('src').resolve()))
from device_store import DeviceStore,preserve_identity,normalized
from fleet import Fleet
import device_manager as dm
A='00008030-0011223344556677';B='00008030-008899AABBCCDDEE'
class StoreManagement(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.scope=patch.object(dm,'DATA',self.root);self.scope.start();self.addCleanup(self.scope.stop)
  self.trash=self.root/'trash';self.trash.mkdir()
  self.store=DeviceStore(self.root,{'keywords':['default']},trash=lambda p:shutil.move(str(p),self.trash))
  self.store.add({'udid':A,'name':'Phone A','ios':'26.6'});self.store.add({'udid':B,'name':'Phone B'})
 def test_rename_persists_across_scans_relaunch_and_stale_settings_save(self):
  stale=self.store.profiles()[0];self.store.rename(A,'  测试手机  ')
  self.store.add({'udid':A,'name':'System renamed','ios':'27.0.1'})
  self.store.save(stale)
  cfg=next(c for c in DeviceStore(self.root,{}).profiles() if c['device_udid']==A)
  self.assertEqual(cfg['device_name'],'测试手机');self.assertEqual(cfg['device_original_name'],'System renamed')
  self.assertEqual(preserve_identity(stale,self.store.path(A))['device_alias'],'测试手机')
 def test_existing_profile_without_alias_migrates_without_reenrollment(self):
  old={'device_udid':A,'device_name':'Old name','keywords':['retain']};dm.write_json(self.store.path(A),old)
  cfg=self.store.add({'udid':A,'name':'Current name'})
  self.assertEqual(cfg['device_original_name'],'Current name');self.assertEqual(cfg['device_name'],'Current name');self.assertEqual(cfg['keywords'],['retain'])
 def test_rename_validation_and_same_name_devices(self):
  for name in (' ','x'*41,'one\ntwo'):
   with self.assertRaises(ValueError):self.store.rename(A,name)
  self.store.rename(A,'same');self.store.rename(B,'same');self.assertEqual([c['device_name'] for c in self.store.profiles()],['same','same'])
 def test_delete_moves_whole_directory_and_preserves_peers_and_shared_data(self):
  directory=self.store.path(A).parent
  for name in ('signing.json','connection.json','test.log','DerivedData/product'):
   p=directory/name;p.parent.mkdir(exist_ok=True);p.write_text('fixture')
  shared=self.root/'ports.json';shared.write_text('shared')
  peer=self.store.path(B).read_bytes();ids=self.store.remove(A)
  self.assertEqual(ids,[A]);self.assertFalse(directory.exists());self.assertEqual(self.store.path(B).read_bytes(),peer);self.assertEqual(shared.read_text(),'shared')
  archived=next(self.trash.iterdir())/A;self.assertEqual((archived/'DerivedData/product').read_text(),'fixture')
 def test_deleted_phone_not_resurrected_by_scan_old_template_or_stale_save(self):
  stale=self.store.profiles()[0];self.store.remove(A)
  self.assertIsNone(self.store.add({'udid':A,'name':'Late scan'}))
  with self.assertRaises(ValueError):self.store.save(stale)
  reopened=DeviceStore(self.root,{'device_udid':A,'device_name':'legacy'})
  self.assertEqual([c['device_udid'] for c in reopened.profiles()],[B])
 def test_manual_readd_starts_from_defaults_and_discards_interrupted_remnants(self):
  self.store.rename(A,'alias');cfg=self.store.profiles()[0];cfg['keywords']=['private previous'];self.store.save(cfg);self.store.remove(A)
  dm.write_json(self.store.path(A),cfg) # Simulate a late write / interrupted removal.
  self.store.path(A).parent.joinpath('signing.json').write_text('obsolete')
  fresh=self.store.add({'udid':A,'name':'System name'},explicit=True)
  self.assertEqual(fresh['keywords'],['default']);self.assertEqual(fresh['device_name'],'System name');self.assertNotIn('device_alias',fresh)
  self.assertFalse((self.store.path(A).parent/'signing.json').exists());self.assertFalse(self.store.is_removed(A))
 def test_trash_failure_rolls_back_config_and_removal_marker(self):
  before=self.store.path(A).read_bytes();self.store.trash=Mock(side_effect=OSError('trash denied'))
  with self.assertRaises(OSError):self.store.remove(A)
  self.assertEqual(self.store.path(A).read_bytes(),before);self.assertFalse(self.store.is_removed(A));self.assertEqual(len(self.store.profiles()),2)
 def test_cleanup_failure_preserves_all_files(self):
  with self.assertRaises(RuntimeError):self.store.remove(A,Mock(side_effect=RuntimeError('in use')))
  self.assertFalse(self.store.is_removed(A));self.assertTrue(self.store.path(A).exists())
 def test_merged_discovery_alias_is_removed_with_its_target(self):
  alias='e'*32;self.store.add({'udid':alias,'name':'discovery'})
  dm.write_json(self.store.path(alias).parent/'connection.json',{'merged_into':A})
  self.assertEqual(set(self.store.remove(A)),{A,alias});self.assertTrue(self.store.is_removed(alias));self.assertFalse(self.store.path(alias).exists())
 def test_registry_corruption_never_silently_reenrolls_a_phone(self):
  (self.root/'device-registry.json').write_text('invalid')
  with self.assertRaises(ValueError):self.store.add({'udid':A})
 def test_id_normalization_preserves_tombstone_across_case_and_hyphens(self):
  self.store.remove(A);self.assertTrue(self.store.is_removed(A.replace('-','').lower()))

class FleetManagement(unittest.TestCase):
 def fleet(self):
  f=Fleet.__new__(Fleet);f.panels={};f.pending_starts={};f.pending_all=None;f.device_window=None;f.location_windows={};f.closing=False;f.scanning=False
  f.locations=SimpleNamespace(snapshot=lambda _: {'phase':'idle','guard_enabled':False},lock=threading.RLock(),sessions={})
  f.usb_ids=set();f.usb_checking=False;f.next_usb_check=float('inf');f.usb_refresh_pending=False;f.events=queue.Queue();f.status=Mock();f.root=Mock();f.redraw=Mock();f.refresh=Mock();f.store=Mock();f.store.is_removed.return_value=False;return f
 def test_busy_tasks_pending_setup_and_location_block_removal(self):
  f=self.fleet();p=SimpleNamespace(worker=Mock());p.worker.is_alive.return_value=True;f.panels[A]=p
  self.assertIn('停止',f.removal_blocker(A));f.panels={};f.pending_starts[A]={};self.assertIn('安排',f.removal_blocker(A))
  f.pending_starts={};f.device_window=SimpleNamespace(busy=True);self.assertIn('配置',f.removal_blocker(A));f.device_window=None
  f.locations.snapshot=lambda _: {'guard_enabled':True};self.assertIn('定位',f.removal_blocker(A))
 def test_usb_change_queues_only_one_refresh_and_failure_keeps_last_result(self):
  f=self.fleet();f.scanning=True;f.events.put(('usb_devices',{normalized(A)}));f.poll()
  self.assertTrue(f.usb_refresh_pending);f.refresh.assert_not_called();self.assertEqual(f.usb_ids,{normalized(A)})
  f.events.put(('usb_devices',None));f.poll();self.assertEqual(f.usb_ids,{normalized(A)})
  f.scanning=False;f.poll();f.refresh.assert_called_once();self.assertFalse(f.usb_refresh_pending)
 def test_late_scan_results_cannot_enroll_deleted_device(self):
  f=self.fleet();f.scanning=True;f.scan_progress=Mock();f.refresh_button=Mock();f.online={};f.store.is_removed.side_effect=lambda i:i==A
  f.events.put(('devices',[{'udid':A,'paired':True},{'udid':B,'paired':True}]))
  f.poll();self.assertNotIn(A,f.online);f.store.add.assert_called_once_with({'udid':B,'paired':True})
 def test_late_remote_result_cannot_revive_deleted_phone(self):
  f=self.fleet();f.last_refresh=None;f.online={};f.store.is_removed.return_value=True
  f.events.put(('remote_devices',(1,[{'udid':A,'paired':True}])));f.poll();self.assertNotIn(A,f.online)
