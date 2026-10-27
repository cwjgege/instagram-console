import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,TransportLost,Halt
from page_state import PageBlocked
CFG=json.loads(Path('src/default-config.json').read_text())
class SearchFallback(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=1000.;self.events=[]
  self.clock=patch('runner.time.monotonic',side_effect=lambda:self.now);self.clock.start();self.addCleanup(self.clock.stop)
  self.r=Runner({**CFG,'enable_search':True,'runtime_dir':self.tmp.name,'max_searches_per_run':10},notify=self.events.append)
  self.r.rng=Mock();self.r.rng.uniform.side_effect=lambda a,b:a;self.r.diagnose=Mock()
  self.r.return_from_search=Mock(side_effect=lambda:self.advance(12));self.r.recover_connection=Mock(side_effect=lambda:self.advance(20))
 def advance(self,seconds):self.now+=seconds
 def fail(self,term):self.r.search_attempts+=1;raise NavigationLost('search navigation failed')
 def test_recovers_then_cools_down_before_next_search_and_resets_on_success(self):
  r=self.r;r.search=Mock(side_effect=self.fail)
  self.assertFalse(r.search_optional('AI'));self.assertEqual(r.search_retry_at,1192)
  self.assertFalse(r.search_optional('AI'));r.search.assert_called_once()
  self.now=1191;self.assertFalse(r.search_ready());self.now=1192
  r.search=Mock();self.assertTrue(r.search_optional('AI'));self.assertEqual(r.search_consecutive_failures,0);self.assertIsNone(r.search_retry_at)
 def test_transport_failure_recovers_connection_instead_of_clicking_dead_session(self):
  r=self.r;r.search=Mock(side_effect=TransportLost('lost'))
  self.assertFalse(r.search_optional('AI'));r.recover_connection.assert_called_once();r.return_from_search.assert_not_called();self.assertEqual(r.search_retry_at,1200)
 def test_return_navigation_connection_loss_also_reconnects(self):
  r=self.r;r.search=Mock(side_effect=self.fail);r.return_from_search.side_effect=TransportLost('lost')
  self.assertFalse(r.search_optional('AI'));r.recover_connection.assert_called_once()
 def test_failed_recovery_propagates_for_task_error_notification(self):
  r=self.r;r.search=Mock(side_effect=self.fail);r.return_from_search.side_effect=RuntimeError('cannot return')
  with self.assertRaisesRegex(RuntimeError,'cannot return'):r.search_optional('AI')
  self.assertIsNone(r.search_retry_at);self.assertIsNone(r.search_deadline);self.assertIsNone(r.search_overall_deadline)
 def test_unconfirmed_return_is_terminal_not_a_silent_disabled_feature(self):
  r=self.r;r.search=Mock(side_effect=self.fail);r.return_from_search.side_effect=NavigationLost('unknown page')
  with self.assertRaisesRegex(RuntimeError,'未能确认'):r.search_optional('AI')
  self.assertIsNone(r.search_retry_at)
 def test_stop_and_account_checks_do_not_enter_retry_flow(self):
  for exc in (Halt('stop'),PageBlocked('verify')):
   r=self.r;r.search=Mock(side_effect=exc)
   with self.assertRaises(type(exc)):r.search_optional('AI')
   r.return_from_search.assert_not_called();r.recover_connection.assert_not_called();self.assertEqual(r.search_failures,0)
 def test_three_consecutive_failures_disable_only_search(self):
  r=self.r;r.search=Mock(side_effect=self.fail)
  for i in range(3):
   self.assertFalse(r.search_optional('AI'))
   if i<2:self.now=r.search_retry_at
  self.assertEqual(r.search_consecutive_failures,3);self.assertFalse(r.search_ready());self.assertIsNone(r.search_retry_at)
  self.assertTrue(any(e['event']=='feature_disabled' for e in self.events));self.assertFalse(r.stop.is_set())
 def test_retry_never_exceeds_configured_search_attempt_limit(self):
  r=self.r;r.cfg['max_searches_per_run']=1;r.search=Mock(side_effect=self.fail)
  self.assertFalse(r.search_optional('AI'));self.now+=10000
  self.assertFalse(r.search_optional('AI'));r.search.assert_called_once();self.assertIsNone(r.search_retry_at)
 def test_deadlines_are_cleared_before_recovery_commands(self):
  r=self.r
  def failure(term):r.search_deadline=self.now-1;raise NavigationLost('failed')
  def recover():self.assertIsNone(r.search_deadline);self.assertIsNone(r.search_overall_deadline)
  r.search=failure;r.return_from_search.side_effect=recover
  self.assertFalse(r.search_optional('AI'))
 def test_full_loop_keeps_swiping_during_cooldown_after_long_failed_search(self):
  r=self.r;r.cfg.update(device_udid='A'*20,max_searches_per_run=2,max_swipes_per_run=90,enable_likes=False,enable_comments=False,enable_saves=False);r.seconds=700
  r.connect=Mock();r.reels=Mock();r.watch_video=lambda *a:self.advance(10);r.wait=lambda s:self.advance(s);r.consider_like=Mock()
  r.rng.random.return_value=1;r.rng.choices.return_value=[1];r.rng.choice.return_value='AI'
  counter=[0];attempts=[];recoveries=[]
  def post():counter[0]+=1;return dict(key=str(counter[0]),author='demo',caption='unrelated',nodes=[])
  r.read_reel=post;r.swipe=lambda _:setattr(r,'swipes',r.swipes+1)
  def search(term):
   r.search_attempts+=1;attempts.append((self.now,r.swipes))
   if len(attempts)==1:self.advance(200);raise NavigationLost('failed')
  def recover():self.advance(12);recoveries.append(self.now)
  r.search=search;r.return_from_search.side_effect=recover
  with patch.dict('os.environ',{'IG_CONSOLE_LOCK_DIR':str(Path(self.tmp.name)/'locks')}):self.assertEqual(r.run(),'complete')
  self.assertEqual(len(attempts),2);self.assertGreaterEqual(attempts[1][0]-recoveries[0],180);self.assertGreater(attempts[1][1],attempts[0][1])


class CooldownDisplay(unittest.TestCase):
 def test_cooldown_is_running_not_an_attention_error(self):
  from fleet import Fleet
  from types import SimpleNamespace
  f=Fleet.__new__(Fleet);udid='A'*20;p=SimpleNamespace(worker=Mock(),stop_event=Mock(),status=Mock(),runner=SimpleNamespace(feature_state={'search':{'enabled':False,'reason':'等待搜索','cooldown':True}},pause_state='running'))
  p.worker.is_alive.return_value=True;p.stop_event.is_set.return_value=False;p.status.get.return_value=''
  f.panels={udid:p};f.online={udid:{'paired':True,'developer':True}};f.errors={};f.scanning=False;f.pending_starts={};f.pending_all=None
  with patch('fleet.dm.signing_record',return_value={}),patch('fleet.dm.expiry_state',return_value=('valid',24)),patch('fleet.dm.setup_status',return_value=None),patch('fleet.connections.load',return_value={'mode':'wifi'}):
   view=f.device_view({'device_udid':udid});self.assertEqual(view['tag'],'running');self.assertIn('搜索冷却',view['state'])
