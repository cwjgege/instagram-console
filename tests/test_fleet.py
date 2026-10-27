import concurrent.futures,json,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
import device_manager as dm,runtime_manager as rm
from fleet import DeviceStore,Fleet

class FleetIsolation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.scope=patch.object(dm,'DATA',self.root);self.scope.start()
 def tearDown(self):self.scope.stop();self.tmp.cleanup()
 def test_concurrent_devices_receive_stable_distinct_ports(self):
  ids=[f'{n:040x}' for n in range(1,9)]
  with concurrent.futures.ThreadPoolExecutor(8) as pool:values=list(pool.map(rm.endpoints,ids))
  ports=[e[k] for e in values for k in ('appium','wda','mjpeg')]
  self.assertEqual(len(ports),len(set(ports)))
  self.assertEqual(values,[rm.endpoints(i) for i in ids])
 def test_migration_and_configuration_are_independent(self):
  one='a'*40;two='b'*40
  store=DeviceStore(self.root,{'device_udid':one,'keywords':['AI video'],'comment_rules':[{'text':'hello'}]})
  first=store.profiles()[0];second=store.add({'udid':two})
  second['keywords'].append('animation');second['comment_rules'][0]['text']='changed';store.save(second)
  self.assertEqual(store.profiles()[0]['keywords'],['AI video'])
  self.assertEqual(store.profiles()[0]['comment_rules'][0]['text'],'hello')
  self.assertNotEqual(first['runtime_dir'],second['runtime_dir'])
 def test_corrupt_device_configuration_does_not_hide_other_devices(self):
  store=DeviceStore(self.root,{'keywords':['AI video']});store.add({'udid':'a'*40});store.add({'udid':'b'*40})
  store.path('a'*40).write_text('invalid json')
  profiles=store.profiles();self.assertEqual(len(profiles),2)
  self.assertIn('_load_error',profiles[0]);self.assertNotIn('_load_error',profiles[1])
 def test_failed_start_does_not_prevent_other_device_start(self):
  fleet=Fleet.__new__(Fleet);fleet.online={'a':{'paired':True,'developer':True},'b':{'paired':True,'developer':True}};fleet.errors={};fleet.redraw=Mock()
  bad=Mock();bad.start.side_effect=RuntimeError('offline');good=Mock()
  fleet.console=lambda udid:bad if udid=='a' else good
  fleet.start_device('a');fleet.start_device('b')
  self.assertIn('offline',fleet.errors['a']);good.start.assert_called_once()
 def test_stopping_one_device_does_not_touch_peer(self):
  fleet=Fleet.__new__(Fleet);a=Mock();b=Mock();fleet.panels={'a':a,'b':b};fleet.redraw=Mock()
  fleet.stop_device('a');a.stop.assert_called_once();b.stop.assert_not_called();self.assertIsNone(a.pending_start)
 def test_port_storage_rejects_unsafe_identifier(self):
  with self.assertRaises(ValueError):rm.endpoints('../other')
 def test_corrupt_port_map_cannot_assign_two_devices_to_same_listener(self):
  ports={'appium':19000,'wda':19001,'mjpeg':19002}
  dm.write_json(self.root/'ports.json',{'a'*40:ports,'b'*40:ports})
  with self.assertRaisesRegex(RuntimeError,'重复'):rm.endpoints('a'*40)
 def test_discovery_retains_modern_device_when_legacy_probe_fails(self):
  modern={'udid':'00008030-0011223344556677','identifier':'core','name':'test','paired':True,'developer':True}
  old='a'*40;physical={modern['udid'].replace('-','').lower(),old}
  with patch.object(dm,'usb_serials',return_value=physical),patch.object(dm,'coredevice',return_value={}),patch.object(dm,'wired_iphones',return_value=[modern]),patch.object(dm.subprocess,'run',return_value=Mock(returncode=0,stdout='[]')),patch.object(rm,'probe_legacy',side_effect=RuntimeError('legacy failed')):
   found=dm.scan_devices()
  self.assertEqual(len(found),2);self.assertTrue(found[0]['paired']);self.assertFalse(found[1]['paired'])

if __name__=='__main__':unittest.main()

class FleetInteraction(unittest.TestCase):
 def fleet(self):
  f=Fleet.__new__(Fleet);f.scanning=True;f.closing=False;f.pending_starts={};f.pending_all=None;f.status=Mock();f.redraw=Mock();f.panels={};f.errors={};f.usb_ids=set();f.usb_refresh_pending=False;f.usb_checking=False;f.next_usb_check=float('inf');f.store=Mock();f.store.is_removed.return_value=False;return f
 def test_start_during_detection_waits_without_reporting_offline(self):
  f=self.fleet();f.start_device('a'*40,test=True)
  self.assertEqual(f.pending_starts['a'*40],{'test':True});self.assertEqual(f.errors,{})
 def test_all_start_during_detection_queues_options(self):
  f=self.fleet();f.start_all(test=True)
  self.assertEqual(f.pending_all,{'test':True})
 def test_stop_all_cancels_pending_start(self):
  f=self.fleet();f.start_all(test=True);f.start_device('a'*40);f.stop_all()
  self.assertIsNone(f.pending_all);self.assertFalse(f.pending_starts)
 def test_stopping_one_waiting_device_preserves_other_request(self):
  f=self.fleet();f.pending_starts={'a':{},'b':{}};f.stop_device('a');self.assertEqual(f.pending_starts,{'b':{}})
 def test_finishing_detection_executes_queued_start_once(self):
  import queue
  f=self.fleet();f.events=queue.Queue();f.online={};f.events.put(('devices',[]));f.pending_all={'test':True}
  f.scan_progress=Mock();f.refresh_button=Mock();f.store=Mock();f.root=Mock();f.start_all=Mock()
  f.poll();f.start_all.assert_called_once_with(test=True);self.assertIsNone(f.pending_all);self.assertFalse(f.scanning)
 def test_failed_detection_cancels_pending_start(self):
  import queue
  f=self.fleet();f.events=queue.Queue();f.events.put(('error','USB failure'));f.pending_all={};f.pending_starts={'a':{}}
  f.scan_progress=Mock();f.refresh_button=Mock();f.root=Mock()
  f.poll();self.assertIsNone(f.pending_all);self.assertFalse(f.pending_starts)
 def test_partial_search_failure_is_visible_while_browsing_continues(self):
  from types import SimpleNamespace
  f=self.fleet();f.scanning=False;f.online={'a':{'paired':True,'developer':True}}
  p=SimpleNamespace(worker=Mock(),stop_event=threading.Event(),status=Mock(),runner=SimpleNamespace(feature_state={'search':{'enabled':False,'reason':'结果页面未识别'}}))
  p.worker.is_alive.return_value=True;p.status.get.return_value='浏览视频';f.panels={'a':p}
  with patch.object(dm,'signing_record',return_value=None):view=f.device_view({'device_udid':'a'})
  self.assertTrue(view['alive']);self.assertIn('搜索暂停',view['state']);self.assertIn('结果页面未识别',view['detail'])
 def test_detection_detail_does_not_claim_device_is_disconnected(self):
  f=self.fleet();f.online={};f.pending_all={}
  with patch.object(dm,'signing_record',return_value=None):view=f.device_view({'device_udid':'a'*40})
  self.assertIn('检测',view['state']);self.assertIn('检测',view['detail']);self.assertNotIn('请连接',view['detail'])

 def test_remote_probe_failure_is_visible_and_does_not_claim_usb_is_missing(self):
  f=self.fleet();f.scanning=False;udid='a'*40;f.online={udid:{'paired':False,'developer':False,'connection_error':'TimeoutError: control port timed out'}}
  with patch.object(dm,'signing_record',return_value=None),patch.object(dm,'setup_status',return_value=None),patch('connections.load',return_value={'mode':'remote'}):view=f.device_view({'device_udid':udid})
  self.assertFalse(view['ready']);self.assertEqual(view['state'],'控制连接异常');self.assertIn('control port timed out',view['detail']);self.assertNotIn('请连接这台手机',view['detail'])
 def test_background_health_recovers_remote_without_manual_refresh(self):
  import queue
  f=self.fleet();f.scanning=False;f.last_refresh=None;f.online={};f.events=queue.Queue();f.root=Mock()
  row={'udid':'a'*40,'paired':True,'developer':True,'transport':'remote'};f.events.put(('remote_devices',(1,[row])))
  f.poll();self.assertEqual(f.online[row['udid']],row);self.assertFalse(f.network_scanning);self.assertIn('1 台',f.status.set.call_args.args[0])
 def test_old_background_health_cannot_overwrite_new_manual_detection(self):
  import queue
  f=self.fleet();f.scanning=False;f.last_refresh=10;f.online={'a':{'paired':True}};f.events=queue.Queue();f.root=Mock()
  f.events.put(('remote_devices',(1,[{'udid':'a','paired':False}])))
  f.poll();self.assertTrue(f.online['a']['paired'])
