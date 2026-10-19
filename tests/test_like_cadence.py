import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,validate_motion,AUTOMATION_DEFAULTS
CFG=json.loads(Path('src/default-config.json').read_text())
class LikeCadence(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.r=Runner({**CFG,'runtime_dir':self.temp.name,'enable_likes':True,'like_interval_min':3,'like_interval_max':3},notify=lambda _:None)
  self.r.toggle_on=Mock()
 def tearDown(self):self.temp.cleanup()
 def post(self,key,related=True):self.r.consider_like({'key':key},related)
 def test_waits_for_distinct_videos_then_next_interval(self):
  for key in ['a','a','a','b']:self.post(key)
  self.r.toggle_on.assert_not_called();self.post('c');self.assertEqual(self.r.toggle_on.call_count,1)
  for key in ['d','e']:self.post(key)
  self.assertEqual(self.r.toggle_on.call_count,1);self.post('f');self.assertEqual(self.r.toggle_on.call_count,2)
 def test_due_on_unrelated_video_waits_for_matching_content(self):
  for key in ['a','b','c','d']:self.post(key,False)
  self.r.toggle_on.assert_not_called();self.post('e');self.r.toggle_on.assert_called_once()
 def test_daily_limit_and_previous_reservation_are_respected(self):
  self.r.like_next=1;self.r.daily['posts']['a']={'likes':'pending'};self.post('a')
  self.r.daily['likes']=self.r.cfg['max_likes_per_day'];self.post('b');self.r.toggle_on.assert_not_called()
 def test_browse_only_and_disabled_never_interact(self):
  self.r.like_next=1;self.r.browse_only=True;self.post('a');self.r.browse_only=False
  self.r.cfg['enable_likes']=False;self.post('b');self.r.toggle_on.assert_not_called()
 def test_uncertain_write_propagates_without_automatic_retry(self):
  self.r.like_next=1;self.r.toggle_on.side_effect=RuntimeError('unconfirmed')
  with self.assertRaisesRegex(RuntimeError,'unconfirmed'):self.post('a')
  self.post('a');self.r.toggle_on.assert_called_once();self.assertTrue(self.r.in_interaction)
 def test_bad_interval_rejected(self):
  for change in [{'like_interval_min':0},{'like_interval_min':9,'like_interval_max':3},{'like_interval_max':101}]:
   with self.assertRaises(ValueError):validate_motion({**AUTOMATION_DEFAULTS,**change})
