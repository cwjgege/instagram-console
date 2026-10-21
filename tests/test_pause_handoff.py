import json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,ManualResume,Halt,NavigationLost
from types import SimpleNamespace
class Handoff(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();cfg=json.loads(Path('src/default-config.json').read_text());cfg['runtime_dir']=self.tmp.name
  self.r=Runner(cfg);self.r.pause_enabled=True;self.r.deadline=time.monotonic()+.05;self.r.current_post_key='old';self.r.daily['posts']['old']={'comments':'pending'}
  self.r.snapshot=Mock(return_value='tree');self.r.observe=Mock(return_value=SimpleNamespace(kind='reels'));self.r.reels=Mock();self.r.read_reel=Mock(return_value={'key':'new'});self.r.mobile=Mock(return_value={'bundleId':'com.burbn.instagram'})
 def tearDown(self):self.r.stop.set();self.tmp.cleanup()
 def launch(self):
  self.result=[]
  def work():
   try:self.r.check()
   except BaseException as e:self.result.append(type(e))
  self.r.request_pause();self.t=threading.Thread(target=work);self.t.start()
  deadline=time.monotonic()+2
  while self.r.pause_state!='paused' and time.monotonic()<deadline:time.sleep(.005)
  self.assertEqual(self.r.pause_state,'paused')
 def test_no_commands_while_paused_then_unwinds_and_preserves_pending_ledger(self):
  self.launch();time.sleep(.08);self.r.mobile.assert_not_called();self.r.snapshot.assert_not_called()
  self.r.request_resume();self.t.join(2);self.assertFalse(self.t.is_alive());self.assertEqual(self.result,[ManualResume]);self.assertEqual(self.r.pause_state,'running')
  self.assertEqual(self.r.handoff_skip_keys,{'old','new'});self.assertTrue(self.r.skip_interactions_once);self.assertEqual(self.r.daily['posts']['old']['comments'],'pending');self.assertGreater(self.r.deadline,time.monotonic()-.04)
 def test_stop_wakes_paused_worker_without_resuming_phone(self):
  self.launch();self.r.stop.set();self.t.join(2);self.assertEqual(self.result,[Halt]);self.r.snapshot.assert_not_called()
 def test_pause_defers_until_current_interaction_finishes(self):
  self.r.in_interaction=True;self.r.request_pause();self.r.check();self.assertEqual(self.r.pause_state,'pausing');self.r.snapshot.assert_not_called()
 def test_unfinished_manual_input_stays_paused(self):
  blocked=threading.Event()
  def observe(tree):blocked.set();return SimpleNamespace(kind='input_unknown')
  self.r.observe=observe;self.launch();self.r.request_resume();self.assertTrue(blocked.wait(2));time.sleep(.05);self.assertEqual(self.r.pause_state,'paused');self.r.reels.assert_not_called();self.r.stop.set();self.t.join(2)
 def test_panel_buttons_only_address_current_live_runner(self):
  from panel import Panel
  p=Panel.__new__(Panel);p.worker=Mock();p.worker.is_alive.return_value=True;p.stop_event=threading.Event();p.runner=Mock();p.status=Mock()
  p.pause_hosting();p.runner.request_pause.assert_called_once();p.resume_hosting();p.runner.request_resume.assert_called_once()
  p.stop_event.set();p.pause_hosting();self.assertEqual(p.runner.request_pause.call_count,1)
 def test_workspace_handoff_is_isolated_to_selected_device(self):
  from fleet import Fleet
  f=Fleet.__new__(Fleet);first=Mock();other=Mock();f.panels={'first':first,'other':other};f.selected=lambda:'first';f.redraw=Mock()
  f.hosting_action('pause_hosting');first.pause_hosting.assert_called_once();other.pause_hosting.assert_not_called()
 def test_paused_session_keepalive_only_reads_appium_settings(self):
  self.r.path='/session/test';self.r.http=Mock(return_value={})
  self.assertTrue(self.r.pause_keepalive());self.r.http.assert_called_once_with('GET','/session/test/timeouts',timeout=3);self.r.mobile.assert_not_called()
  self.r.http.side_effect=RuntimeError('expired');self.assertFalse(self.r.pause_keepalive())
