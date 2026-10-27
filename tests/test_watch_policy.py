import sys,json,tempfile,unittest,itertools
from pathlib import Path
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from watch_policy import playback_timing,playback_progress,playback_finished,watch_plan,observed_progress
from runner import Runner,WatchBudgetExpired,TransportLost
CFG=json.loads(Path('src/default-config.json').read_text())
def slider(value,**attrs):return ET.Element('XCUIElementTypeSlider',{'visible':'true','name':'Video playback','value':value,**attrs})
class Policy(unittest.TestCase):
 def setUp(self):self.rng=Mock();self.rng.random.return_value=0;self.rng.uniform.side_effect=lambda a,b:b
 def test_related_known_short_and_long_videos_have_no_seconds_cap(self):
  for duration in (10,600,1800):
   p=watch_plan({'playback':{'elapsed':3,'duration':duration}},True,self.rng)
   self.assertIsNone(p['seconds']);self.assertTrue(p['complete_intent']);self.assertEqual(p['pass_index'],1);self.assertEqual(p['duration'],duration)
 def test_second_cycle_can_choose_completion(self):
  self.rng.random.return_value=0
  p=watch_plan({'progress':20},True,self.rng,pass_index=2)
  self.assertIsNone(p['seconds']);self.assertTrue(p['complete_intent']);self.assertEqual(p['pass_index'],2)
 def test_unknown_related_progress_does_not_invent_a_duration(self):
  p=watch_plan({},True,self.rng);self.assertEqual(p['basis'],'unavailable');self.assertIsNone(p['seconds'])
 def test_unrelated_still_has_short_random_budget_even_if_duration_known(self):
  self.rng.random.return_value=1
  p=watch_plan({'playback':{'elapsed':0,'duration':600}},False,self.rng)
  self.assertEqual(p['seconds'],5);self.assertEqual(p['basis'],'short');self.rng.random.assert_called_once()
 def test_parser_requires_player_control_and_unambiguous_timing(self):
  self.assertEqual(playback_timing([slider('00:03 / 00:10')]),{'elapsed':3,'duration':10})
  self.assertIsNone(playback_timing([slider('30%')]))
  self.assertIsNone(playback_timing([slider('00:03 / 00:10'),slider('00:03 / 00:20')]))
  self.assertIsNone(playback_timing([ET.Element('XCUIElementTypeStaticText',visible='true',label='Video playback 00:03 / 00:10')]))
 def test_real_progress_other_node_supported_without_caption_false_positives(self):
  self.assertEqual(playback_progress([ET.Element('XCUIElementTypeOther',visible='true',name='Video progress: 62 percent')]),62)
  self.assertIsNone(playback_progress([ET.Element('XCUIElementTypeStaticText',visible='true',label='Video progress: 62 percent')]))
 def test_elapsed_duration_can_supply_progress(self):self.assertEqual(observed_progress({'playback':{'elapsed':9,'duration':10}}),90)
 def test_small_jitter_ignored_but_wrap_between_samples_is_detected(self):
  self.assertFalse(playback_finished(50,49));self.assertTrue(playback_finished(80,40));self.assertTrue(playback_finished(90,4))

class Watching(unittest.TestCase):
 def scenario(self,sequence,replay=False):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=100.;self.events=[]
  r=Runner({**CFG,'runtime_dir':self.tmp.name},notify=self.events.append);r.rng=Mock();r.rng.random.return_value=0;r.rng.random.side_effect=None if replay else [0,1];r.rng.uniform.return_value=4
  r.wait=Mock(side_effect=lambda seconds:setattr(self,'now',self.now+seconds))
  def read():
   self.now+=.1
   return {'key':'clip','progress':next(sequence)}
  r.read_reel=Mock(side_effect=read)
  return r,patch('runner.time.monotonic',side_effect=lambda:self.now)
 def test_related_keeps_watching_beyond_25_seconds_while_progress_advances(self):
  r,clock=self.scenario(iter(range(2,100)))
  with clock:r.watch_video({'key':'clip','progress':1},True)
  self.assertGreater(self.now-100,100);self.assertTrue(r.video_watch['done']);self.assertEqual(self.events[-1]['event'],'watch_complete')
 def test_second_cycle_is_allowed_without_double_counting_end_samples(self):
  r,clock=self.scenario(iter([98,99,10,50,98]),True)
  with clock:r.watch_video({'key':'clip','progress':90},True)
  self.assertEqual(r.read_reel.call_count,5);self.assertEqual(sum(e['event']=='watch_replay_complete' for e in self.events),1)
 def test_wrap_completion_can_start_a_second_cycle_immediately(self):
  r,clock=self.scenario(iter([10,50,99]),True)
  with clock:r.watch_video({'key':'clip','progress':85},True)
  self.assertEqual(r.read_reel.call_count,3);self.assertTrue(r.video_watch['done'])
 def test_already_finished_video_does_not_restart_after_failed_swipe(self):
  r,clock=self.scenario(iter([]))
  with clock:
   r.watch_video({'key':'clip','progress':99},True);r.watch_video({'key':'clip','progress':10},True)
  r.read_reel.assert_not_called();r.wait.assert_not_called();self.assertEqual(r.rng.random.call_count,2)
 def test_unknown_progress_has_a_missing_data_guard(self):
  r,clock=self.scenario(itertools.repeat(None))
  with clock:r.watch_video({'key':'clip'},True)
  self.assertEqual(r.read_reel.call_count,2);self.assertEqual(self.events[-1]['event'],'watch_progress_unavailable')
 def test_frozen_progress_is_a_stall_guard_not_a_healthy_video_cap(self):
  r,clock=self.scenario(itertools.repeat(50))
  with clock:r.watch_video({'key':'clip','progress':50},True)
  self.assertGreaterEqual(self.now-100,30);self.assertLess(self.now-100,33);self.assertEqual(self.events[-1]['event'],'watch_stalled')
 def test_read_failure_routes_to_connection_recovery_instead_of_waiting_forever(self):
  r,clock=self.scenario(iter([]));r.read_reel.side_effect=WatchBudgetExpired()
  with clock,self.assertRaises(TransportLost):r.watch_video({'key':'clip','progress':50},True)
  self.assertIsNone(r.watch_read_deadline)
 def test_manual_stop_remains_effective_during_long_related_watch(self):
  from runner import Halt
  r,clock=self.scenario(itertools.repeat(50));r.stop.set();r.wait=Runner.wait.__get__(r,Runner)
  with clock,self.assertRaises(Halt):r.watch_video({'key':'clip','progress':50},True)
 def test_short_watch_counts_prior_reads_and_does_not_poll_completion(self):
  r,clock=self.scenario(iter([]));r.rng.random.side_effect=None;r.rng.random.return_value=1
  with clock:r.watch_video({'key':'clip','observed_at':98,'progress':50},False)
  r.wait.assert_called_once_with(2);r.read_reel.assert_not_called()
 def test_relevant_first_view_can_exit_without_waiting_for_completion(self):
  r,clock=self.scenario(iter([]));r.rng.random.side_effect=[.9]
  with clock:r.watch_video({'key':'clip','progress':20},True)
  r.read_reel.assert_not_called();r.wait.assert_called_once_with(4);self.assertTrue(r.video_watch['done'])
 def test_brief_second_view_does_not_wait_for_second_completion(self):
  r,clock=self.scenario(iter([98]));r.rng.random.side_effect=[0,0,.9]
  with clock:r.watch_video({'key':'clip','progress':90},True)
  self.assertEqual(r.read_reel.call_count,1);self.assertEqual(self.events[-1]['event'],'watch_replay_brief')
  self.assertTrue(r.video_watch['done']);r.rng.uniform.assert_called_once_with(1,4)
 def test_io_read_still_has_a_request_timeout_without_viewing_cap(self):
  r,clock=self.scenario(iter([]));r.path='/session/test';r.http=Mock(return_value={});r.watch_read_deadline=108
  with clock:r.command('GET','/source')
  self.assertEqual(r.http.call_args.kwargs['timeout'],8)

class Probabilities(unittest.TestCase):
 def plan(self,related,value,second=False):
  rng=Mock();rng.random.return_value=value;rng.uniform.side_effect=lambda a,b:a
  return watch_plan({'progress':20},related,rng,pass_index=2 if second else 1)
 def test_relevant_can_choose_early_exit(self):self.assertFalse(self.plan(True,.9)['complete_intent'])
 def test_irrelevant_can_occasionally_choose_completion(self):self.assertTrue(self.plan(False,.05)['complete_intent'])
 def test_relevance_increases_first_pass_completion_tendency(self):
  self.assertTrue(self.plan(True,.5)['complete_intent']);self.assertFalse(self.plan(False,.5)['complete_intent'])
 def test_second_viewing_is_more_likely_to_exit_early_for_both_categories(self):
  self.assertTrue(self.plan(True,.5)['complete_intent']);self.assertFalse(self.plan(True,.5,True)['complete_intent'])
  self.assertTrue(self.plan(False,.05)['complete_intent']);self.assertFalse(self.plan(False,.05,True)['complete_intent'])
 def test_second_brief_viewing_uses_a_shorter_range(self):
  self.assertEqual(self.plan(True,.9)['seconds'],2);self.assertEqual(self.plan(True,.9,True)['seconds'],1)
