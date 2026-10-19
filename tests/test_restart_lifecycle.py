import json,sys,tempfile,unittest,threading,queue,fcntl,os
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from panel import Panel
from runner import Runner,TransportLost
from app_paths import lock_device
CFG=json.loads(Path('src/default-config.json').read_text())
class RestartLifecycle(unittest.TestCase):
 def panel(self):
  p=Panel.__new__(Panel)
  p.log_ui=Mock();p.root=Mock();p.status=Mock();p.start_button=Mock();p.stop_button=Mock();p.append=Mock();p.set_running=Mock()
  p.worker=Mock();p.worker.is_alive.return_value=True
  p.generation=2;p.run_ui_active=True;p.worker_result=None;p.finished_row=None;p.phase_text='';p.phase_started=0
  p.events=queue.Queue();p.stop_event=threading.Event();p.pending_start=None;p.closing=False;p.stop_requested_at=None
  p.last_start={'test':True,'preview':False,'diagnostic':False};p.run_error='unexpected'
  return p
 def test_pointer_trace_never_reads_text_input(self):
  p=self.panel();p.test_button=Mock();p.diagnostic_button=Mock();widget=Mock();widget.winfo_class.return_value='TEntry'
  widget.get.return_value='secret';event=Mock(widget=widget,type=4,x=5,y=6)
  p.trace_pointer(event);widget.get.assert_not_called();widget.cget.assert_not_called()
  self.assertNotIn('secret',str(p.log_ui.call_args))
 def test_ui_logging_failure_never_blocks_controls(self):
  p=self.panel()
  with patch('panel.Path.mkdir',side_effect=OSError('disk')):
   Panel.log_ui(p,'test')
 def test_completion_does_not_enable_start_until_thread_exit(self):
  p=self.panel();p.events.put(dict(event='finished',status='complete',log_dir='test',generation=2));p.events.put(dict(event='worker_result',status='complete',generation=2))
  p.process_events();p.set_running.assert_not_called()
  p.worker.is_alive.return_value=False;p.process_events();p.set_running.assert_called_once_with(False)
  self.assertFalse(p.run_ui_active)
 def test_stop_then_start_queues_exactly_one_restart_and_preserves_mode(self):
  p=self.panel();p.stop();p.start();p.start()
  self.assertEqual(p.pending_start,p.last_start)
  p.worker.is_alive.return_value=False;p.events.put(dict(event='worker_result',status='stopped',generation=2));p.process_events()
  p.root.after.assert_called_once();p.start=Mock();p.root.after.call_args.args[1]()
  p.start.assert_called_once_with(test=True,preview=False,diagnostic=False)
 def test_running_start_has_feedback_without_second_task(self):
  p=self.panel();p.start();self.assertIn('正在运行',p.status.set.call_args.args[0]);self.assertIsNone(p.pending_start)
 def test_old_completion_cannot_stop_new_run(self):
  p=self.panel();p.events.put(dict(event='worker_result',status='complete',generation=1));p.process_events()
  self.assertIsNone(p.worker_result);p.set_running.assert_not_called()
 def test_error_exit_reenables_start_and_preserves_error(self):
  p=self.panel();p.worker.is_alive.return_value=False
  p.events.put(dict(event='error',message='failed',generation=2));p.events.put(dict(event='worker_result',status='error',generation=2))
  p.process_events();p.set_running.assert_called_once_with(False);self.assertEqual(p.status.set.call_args.args[0],'已停止：failed')
 def test_poll_exception_does_not_kill_future_polling(self):
  p=self.panel();p.process_events=Mock(side_effect=RuntimeError('widget'))
  with patch('panel.traceback.print_exc'):p.poll()
  p.root.after.assert_called_once_with(200,p.poll)
 def test_close_cancels_queued_restart(self):
  p=self.panel();p.pending_start=dict(test=True);p.device_window=None;p.close();self.assertIsNone(p.pending_start);self.assertTrue(p.closing)

class SessionLifecycle(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.base=Path(self.tmp.name).resolve()
  self.env=patch.dict(os.environ,IG_CONSOLE_LOCK_DIR=str(self.base/'locks'));self.env.start()
  self.r=Runner({**CFG,'device_udid':'A'*20,'runtime_dir':str(self.base)},notify=lambda _:None,seconds=0)
  self.r.wait_for_usb=Mock();self.r.wait=Mock()
  (self.base/'active-session.json').write_text(json.dumps(dict(sessionId='old',capabilities={'udid':'A'*20})))
  (self.base/'session-request.json').write_text(json.dumps({'capabilities':{'alwaysMatch':{'appium:udid':'A'*20}}}))
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def test_cached_connection_requires_real_device_response(self):
  with patch('runner.call',side_effect=[{},dict(bundleId='com.burbn.instagram')]) as call:
   self.r.connect()
   self.assertEqual(call.call_count,2);self.assertEqual(call.call_args.args[2]['script'],'mobile: activeAppInfo')
   self.assertEqual(self.r.path,'/session/old')
 def test_stale_session_is_deleted_and_rebuilt(self):
  with patch('runner.call',side_effect=[{},RuntimeError('invalid session id'),None,dict(sessionId='new',capabilities={'udid':'A'*20})]) as call:
   self.r.connect()
   self.assertEqual(call.call_args_list[2].args,('DELETE','/session/old'))
   self.assertFalse(call.call_args_list[3].args[2]['capabilities']['alwaysMatch']['appium:useNewWDA'])
   self.assertEqual(self.r.path,'/session/new')
 def test_old_saved_uninstall_flag_is_ignored_for_new_connection(self):
  (self.base/'active-session.json').unlink()
  (self.base/'session-request.json').write_text(json.dumps({'capabilities':{'alwaysMatch':{'appium:udid':'A'*20,'appium:useNewWDA':True}}}))
  with patch('runner.call',side_effect=[{},dict(sessionId='new',capabilities={'udid':'A'*20})]) as call:
   self.r.connect()
   self.assertFalse(call.call_args.args[2]['capabilities']['alwaysMatch']['appium:useNewWDA'])
 def test_forced_reconnect_does_not_uninstall_phone_component(self):
  (self.base/'session-request.json').write_text(json.dumps({'capabilities':{'alwaysMatch':{'appium:udid':'A'*20,'appium:useNewWDA':True}}}))
  with patch('runner.call',side_effect=[{},None,dict(sessionId='new',capabilities={'udid':'A'*20})]) as call:
   self.r.connect(force_new=True)
   self.assertEqual(call.call_args_list[1].args[:2],('DELETE','/session/old'))
   self.assertFalse(call.call_args.args[2]['capabilities']['alwaysMatch']['appium:useNewWDA'])
 def test_finished_event_is_emitted_after_both_locks_released(self):
  self.r.connect=Mock();self.r.reels=Mock();observed=[]
  def notify(row):
   if row['event']=='finished':
    with lock_device('A'*20), (self.base/'runner.lock').open('a') as f:
     fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);observed.append(True)
  self.r.notify=notify;self.r.run();self.assertEqual(observed,[True])
 def test_managed_completion_cleans_only_own_cached_session(self):
  self.r.managed=True;self.r.path='/session/old';self.r.connect=Mock();self.r.reels=Mock()
  peer=self.base/'peer/active-session.json';peer.parent.mkdir();peer.write_text('{}')
  with patch('runner.call') as transport:self.r.run()
  self.assertEqual(transport.call_args.args,('DELETE','/session/old'))
  self.assertFalse((self.base/'active-session.json').exists());self.assertTrue(peer.exists())
 def test_managed_cleanup_failure_keeps_session_for_next_start_check(self):
  self.r.managed=True;self.r.path='/session/old';self.r.connect=Mock();self.r.reels=Mock()
  with patch('runner.call',side_effect=RuntimeError('offline')):self.r.run()
  self.assertTrue((self.base/'active-session.json').exists())
 def test_save_failure_still_releases_device_lock(self):
  self.r.connect=Mock();self.r.reels=Mock()
  with patch('runner.atomic_json',side_effect=OSError('disk')):
   with self.assertRaises(OSError):self.r.run()
  with lock_device('A'*20):pass
 def test_activation_transport_error_can_recover_safely(self):
  self.r.path='/session/test'
  with patch('runner.call',side_effect=RuntimeError('socket hang up')):
   with self.assertRaises(TransportLost):self.r.mobile('activateApp',bundleId='com.burbn.instagram')
 def test_startup_connection_loss_enters_recovery(self):
  self.r.connect=Mock(side_effect=TransportLost('lost'));self.r.recover_connection=Mock()
  self.assertEqual(self.r.run(),'complete');self.r.recover_connection.assert_called_once()
if __name__=='__main__':unittest.main()
