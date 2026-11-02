import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,SearchReturnExpired,TransportLost,Halt,ManualResume
from page_state import PageBlocked,PageState
CFG=json.loads(Path('src/default-config.json').read_text())

class SearchReturnBudget(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=1000.;self.events=[]
  clock=patch('runner.time.monotonic',side_effect=lambda:self.now);clock.start();self.addCleanup(clock.stop)
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name,'enable_search':True},notify=self.events.append)
  self.r.path='/session/test';self.r.wait=Mock(side_effect=lambda seconds:setattr(self,'now',self.now+seconds))
  self.r.recover_connection=Mock();self.r.control_available=Mock(return_value=True);self.r.diagnose=Mock()
 def test_total_budget_is_not_reset_by_reads_and_cleared_before_rebuild(self):
  r=self.r
  def stalled():
   self.assertEqual(r.search_return_deadline,1090);self.now+=95;raise TransportLost('slow source')
  r.return_from_search=Mock(side_effect=stalled)
  r.recover_connection.side_effect=lambda **kw:self.assertIsNone(r.search_return_deadline)
  r.recover_search_browsing()
  r.return_from_search.assert_called_once();r.recover_connection.assert_called_once_with(single_attempt=True)
  self.assertIsNone(r.search_return_deadline);self.assertTrue(r.skip_interactions_once)
 def test_responsive_window_probe_cannot_prevent_bounded_page_recovery(self):
  r=self.r;r.return_from_search=Mock(side_effect=TransportLost('source stuck'));r.wait_for_page_response=Mock()
  r.recover_search_browsing()
  self.assertEqual(r.return_from_search.call_count,2);r.recover_connection.assert_called_once_with(single_attempt=True)
  r.wait_for_page_response.assert_not_called();r.control_available.assert_not_called()
 def test_read_and_write_requests_share_remaining_return_budget(self):
  r=self.r;r.http=Mock(return_value={});r.search_return_deadline=1003
  r.command('GET','/source');self.assertEqual(r.http.call_args.kwargs['timeout'],3)
  r.command('POST','/element/back/click',{});self.assertEqual(r.http.call_args.kwargs['timeout'],3)
  self.now=1003
  with self.assertRaises(SearchReturnExpired):r.command('POST','/element/back/click',{})
  self.assertEqual(r.http.call_count,2)
 def test_timeout_at_deadline_does_not_retry_read(self):
  r=self.r;r.search_return_deadline=1002
  def slow(*args,**kw):self.now=1002;raise TimeoutError('source timeout')
  r.http=Mock(side_effect=slow)
  with self.assertRaises(SearchReturnExpired):r.command('GET','/source')
  r.http.assert_called_once()
 def test_disabled_navigation_recovery_does_not_rebuild(self):
  r=self.r;r.cfg['recovery_enabled']=False;r.return_from_search=Mock()
  with self.assertRaisesRegex(RuntimeError,'自动恢复已关闭'):r.recover_search_browsing()
  r.return_from_search.assert_not_called();r.recover_connection.assert_not_called()
 def test_manual_stop_and_unknown_draft_never_rebuild_or_write(self):
  for exc in (Halt('stop'),PageBlocked('draft')):
   r=self.r;r.return_from_search=Mock(side_effect=exc)
   with self.assertRaises(type(exc)):r.recover_search_browsing()
   self.assertIsNone(r.search_return_deadline);r.recover_connection.assert_not_called()
 def test_single_rebuild_does_not_fall_back_into_retry_loop(self):
  r=self.r;r.connect=Mock(side_effect=TransportLost('source lost'));r.recover_reels=Mock()
  with self.assertRaisesRegex(RuntimeError,'单次控制连接重建'):Runner.recover_connection(r,single_attempt=True)
  r.connect.assert_called_once_with(force_new=True,quick=False);r.recover_reels.assert_not_called();self.assertEqual(r.reconnections,1)
 def test_single_rebuild_requires_video_confirmation(self):
  r=self.r;r.connect=Mock();r.recover_reels=Mock(side_effect=TransportLost('source still stuck'))
  with self.assertRaisesRegex(RuntimeError,'单次控制连接重建'):Runner.recover_connection(r,single_attempt=True)
  self.assertEqual(r.connect.call_count,1);self.assertFalse(r.skip_interactions_once)
 def test_search_is_not_replayed_after_rebuild_and_cooldown_starts_after_confirmation(self):
  r=self.r;r.search=Mock(side_effect=TransportLost('search click response lost'));r.return_from_search=Mock(side_effect=TransportLost('source stuck'))
  r.rng=Mock();r.rng.uniform.return_value=180
  def rebuilt(**kw):self.now+=30
  r.recover_connection.side_effect=rebuilt
  self.assertFalse(r.search_optional('AI'))
  r.search.assert_called_once();self.assertEqual(r.search_retry_at,1212);self.assertTrue(r.feature_state['search']['cooldown'])
  self.assertTrue(r.skip_interactions_once);self.assertIsNone(r.video_watch)
 def test_failed_rebuild_clears_recovering_status_and_all_deadlines(self):
  r=self.r;r.search=Mock(side_effect=TransportLost('search'));r.return_from_search=Mock(side_effect=TransportLost('source'))
  r.recover_connection.side_effect=RuntimeError('rebuild failed')
  with self.assertRaisesRegex(RuntimeError,'rebuild failed'):r.search_optional('AI')
  self.assertFalse(r.feature_state['search'].get('recovering',False));self.assertIn('尚未恢复',r.feature_state['search']['reason'])
  self.assertIsNone(r.search_return_deadline);self.assertIsNone(r.search_overall_deadline);self.assertIsNone(r.search_deadline)
 def test_recovery_label_has_priority_over_browsing_and_identity_fallback(self):
  from fleet import Fleet
  f=Fleet.__new__(Fleet);udid='A'*20
  p=SimpleNamespace(worker=Mock(),stop_event=Mock(),status=Mock(),runner=SimpleNamespace(feature_state={'search':{'enabled':False,'recovering':True,'reason':'恢复中'},'identity':{'enabled':False,'reason':'缺失'}},pause_state='running'))
  p.worker.is_alive.return_value=True;p.stop_event.is_set.return_value=False;p.status.get.return_value=''
  f.panels={udid:p};f.online={udid:{'paired':True,'developer':True}};f.errors={};f.scanning=False;f.pending_starts={};f.pending_all=None
  with patch('fleet.dm.signing_record',return_value={}),patch('fleet.dm.expiry_state',return_value=('valid',24)),patch('fleet.dm.setup_status',return_value=None),patch('fleet.connections.load',return_value={'mode':'wifi'}):
   view=f.device_view({'device_udid':udid});self.assertEqual(view['state'],'正在恢复视频页');self.assertNotEqual(view['tag'],'running')
 def test_manual_handoff_discards_expired_return_budget_before_resume_checks(self):
  r=self.r;r.search_return_deadline=999;r.snapshot=Mock();r.observe=Mock(return_value=PageState('reels','视频页'));r.reels=Mock();r.read_reel=Mock(return_value={'key':'new'})
  def app_info(*args,**kwargs):self.assertIsNone(r.search_return_deadline);return {'bundleId':'com.burbn.instagram'}
  r.mobile=Mock(side_effect=app_info)
  with self.assertRaises(ManualResume):r.manual_handoff()
  self.assertEqual(r.pause_state,'running');self.assertIsNone(r.search_return_deadline)

if __name__=='__main__':unittest.main()
