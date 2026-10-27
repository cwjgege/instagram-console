import sys,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path('src').resolve()))
from watch_policy import playback_timing,watch_plan
from runner import Runner
CFG=json.loads(Path('src/default-config.json').read_text())
def progress(value,**attrs):
 return ET.Element('XCUIElementTypeSlider',{'visible':'true','name':'Video playback','value':value,**attrs})
class WatchPolicy(unittest.TestCase):
 def setUp(self):
  self.rng=Mock();self.rng.uniform.side_effect=lambda low,high:high
 def test_short_clip_is_not_scheduled_for_many_loops(self):
  plan=watch_plan({'playback':{'elapsed':0,'duration':10}},True,self.rng)
  self.assertAlmostEqual(plan['seconds'],11.5);self.assertEqual(plan['basis'],'duration')
 def test_current_position_is_subtracted(self):
  plan=watch_plan({'playback':{'elapsed':9,'duration':10}},True,self.rng)
  self.assertAlmostEqual(plan['seconds'],2.5)
 def test_long_video_is_sampled_instead_of_waiting_minutes(self):
  self.assertEqual(watch_plan({'playback':{'elapsed':0,'duration':600}},True,self.rng)['seconds'],25)
 def test_unknown_duration_has_a_short_hard_dwell_ceiling(self):
  self.assertEqual(watch_plan({},True,self.rng)['seconds'],10)
  self.assertEqual(watch_plan({},False,self.rng)['seconds'],5)
 def test_invalid_timing_uses_fallback(self):
  for timing in ({'elapsed':0,'duration':float('inf')},{'elapsed':11,'duration':10},{'elapsed':False,'duration':10}):
   self.assertEqual(watch_plan({'playback':timing},True,self.rng)['basis'],'fallback')
 def test_parser_requires_a_player_progress_control(self):
  self.assertEqual(playback_timing([progress('00:03 / 00:10')]),{'elapsed':3,'duration':10})
  self.assertEqual(playback_timing([progress('0:03 of 0:10')]),{'elapsed':3,'duration':10})
  for node in (ET.Element('XCUIElementTypeStaticText',visible='true',label='Video playback 00:03 / 00:10'),progress('30%'),progress('00:03 / 00:10',visible='false'),progress('00:03 / 00:10',name='Upload')):
   self.assertIsNone(playback_timing([node]))
 def test_conflicting_player_controls_are_not_guessed(self):
  self.assertIsNone(playback_timing([progress('00:03 / 00:10'),progress('00:03 / 00:20')]))
 def runner(self,folder):
  r=Runner({**CFG,'runtime_dir':folder},notify=lambda _:None);r.rng=self.rng;r.wait=Mock();return r
 def test_accessibility_read_and_navigation_time_already_count(self):
  with tempfile.TemporaryDirectory() as folder:
   r=self.runner(folder)
   with patch('runner.time.monotonic',return_value=106):r.watch_video({'key':'clip','observed_at':100},True)
   r.wait.assert_called_once_with(4)
 def test_failed_swipe_does_not_restart_random_dwell_for_same_clip(self):
  with tempfile.TemporaryDirectory() as folder:
   r=self.runner(folder)
   with patch('runner.time.monotonic',return_value=100):r.watch_video({'key':'clip','observed_at':100},True)
   r.wait.reset_mock()
   with patch('runner.time.monotonic',return_value=115):r.watch_video({'key':'clip','observed_at':114},True)
   r.wait.assert_not_called();self.assertEqual(self.rng.uniform.call_count,1)
 def test_new_clip_gets_a_new_watch_budget(self):
  with tempfile.TemporaryDirectory() as folder:
   r=self.runner(folder)
   with patch('runner.time.monotonic',return_value=100):r.watch_video({'key':'old'},True)
   with patch('runner.time.monotonic',return_value=115):r.watch_video({'key':'new'},True)
   self.assertEqual(r.wait.call_args.args,(10,));self.assertEqual(self.rng.uniform.call_count,2)
 def test_playback_position_anchor_is_not_double_counted_as_navigation_time(self):
  with tempfile.TemporaryDirectory() as folder:
   r=self.runner(folder)
   with patch('runner.time.monotonic',return_value=108):r.watch_video({'key':'clip','observed_at':100,'timing_at':106,'playback':{'elapsed':5,'duration':10}},True)
   self.assertAlmostEqual(r.wait.call_args.args[0],4.5)

class ProgressWatching(unittest.TestCase):
 def test_real_instagram_progress_other_node_is_supported(self):
  from watch_policy import playback_progress
  node=ET.Element('XCUIElementTypeOther',visible='true',name='Video progress: 62 percent',label='Video progress: 62 percent')
  self.assertEqual(playback_progress([node]),62)
  for bad in (ET.Element('XCUIElementTypeStaticText',visible='true',label='Video progress: 62 percent'),ET.Element('XCUIElementTypeOther',visible='true',name='62 percent of likes')):
   self.assertIsNone(playback_progress([bad]))
 def scenario(self,sequence):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.now=100.;self.events=[]
  r=Runner({**CFG,'runtime_dir':self.tmp.name},notify=self.events.append);r.rng=Mock();r.rng.uniform.return_value=25
  def wait(seconds):self.now+=seconds
  def read():
   self.now+=1
   return {'key':'clip','progress':next(sequence)}
  r.wait=Mock(side_effect=wait);r.read_reel=Mock(side_effect=read)
  return r,patch('runner.time.monotonic',side_effect=lambda:self.now)
 def test_wrap_from_end_to_start_finishes_without_rewatch(self):
  r,clock=self.scenario(iter([10]))
  with clock:r.watch_video({'key':'clip','progress':85},True)
  self.assertLess(self.now-100,4);self.assertEqual(self.events[-1]['event'],'watch_complete')
 def test_near_100_finishes_without_extra_wait(self):
  r,clock=self.scenario(iter([]))
  with clock:r.watch_video({'key':'clip','progress':99},True)
  r.wait.assert_not_called();r.read_reel.assert_not_called()
 def test_small_backwards_jitter_is_not_treated_as_a_loop(self):
  r,clock=self.scenario(iter([49,60,75,98]))
  with clock:r.watch_video({'key':'clip','progress':50},True)
  self.assertEqual(r.read_reel.call_count,4);self.assertEqual(self.events[-1]['event'],'watch_complete')
 def test_frozen_progress_has_a_bounded_stall_exit(self):
  import itertools
  r,clock=self.scenario(itertools.repeat(50))
  with clock:r.watch_video({'key':'clip','progress':50},True)
  self.assertLessEqual(self.now-100,12);self.assertEqual(self.events[-1]['event'],'watch_stalled')
 def test_losing_progress_does_not_reset_wait_budget(self):
  r,clock=self.scenario(iter([None]))
  with clock:r.watch_video({'key':'clip','progress':50,'observed_at':100},True)
  self.assertLessEqual(self.now-100,10)

class ProgressReadBudget(unittest.TestCase):
 def test_progress_http_read_cannot_outlive_watch_budget(self):
  with tempfile.TemporaryDirectory() as folder:
   r=Runner({**CFG,'runtime_dir':folder},notify=lambda _:None);r.path='/session/test';r.http=Mock(return_value={});r.watch_read_deadline=103
   with patch('runner.time.monotonic',return_value=100):r.command('GET','/source')
   self.assertEqual(r.http.call_args.kwargs['timeout'],3)
 def test_expired_progress_read_is_not_treated_as_a_write_failure(self):
  from runner import WatchBudgetExpired
  with tempfile.TemporaryDirectory() as folder:
   r=Runner({**CFG,'runtime_dir':folder},notify=lambda _:None);r.path='/session/test';r.http=Mock();r.watch_read_deadline=99
   with patch('runner.time.monotonic',return_value=100),self.assertRaises(WatchBudgetExpired):r.command('GET','/source')
   r.http.assert_not_called()
