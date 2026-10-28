import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner

CFG=json.loads(Path('src/default-config.json').read_text())

class DurationOnly(unittest.TestCase):
 def run_case(self,legacy=None,manual=False):
  with tempfile.TemporaryDirectory() as folder:
   cfg={**CFG,'runtime_dir':folder,'device_udid':'a'*20,'enable_search':False,
        'enable_likes':False,'enable_saves':False,'enable_comments':False}
   if legacy is not None:cfg['max_swipes_per_run']=legacy
   r=Runner(cfg,seconds=1000 if manual else 151,notify=lambda _:None)
   clock=[0.0];r.connect=Mock();r.reels=Mock();r.wait=Mock()
   r.rng=Mock();r.rng.uniform.side_effect=lambda a,b:a;r.rng.random.return_value=1;r.rng.choices.return_value=[1]
   r.read_reel=lambda:{'key':str(r.swipes),'author':'test','caption':'unrelated','nodes':[]}
   r.watch_video=lambda *args:clock.__setitem__(0,clock[0]+1)
   def swipe(direction):
    r.swipes+=1
    if manual and r.swipes==121:r.stop.set()
   r.swipe=swipe
   with patch('runner.time.monotonic',side_effect=lambda:clock[0]),patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(Path(folder)/'locks')):
    status=r.run()
   return status,r.swipes,clock[0]
 def test_old_limits_do_not_end_browsing_before_its_duration(self):
  for old in (1,120):
   with self.subTest(old=old):
    status,swipes,elapsed=self.run_case(old)
    self.assertEqual(status,'complete');self.assertGreater(swipes,120);self.assertEqual(elapsed,151)
 def test_new_config_needs_no_swipe_limit(self):
  self.assertNotIn('max_swipes_per_run',CFG)
  status,swipes,elapsed=self.run_case()
  self.assertEqual(status,'complete');self.assertGreater(swipes,120);self.assertEqual(elapsed,151)
 def test_manual_stop_still_works_after_120_swipes(self):
  status,swipes,elapsed=self.run_case(120,manual=True)
  self.assertEqual(status,'stopped');self.assertEqual(swipes,121);self.assertLess(elapsed,1000)
