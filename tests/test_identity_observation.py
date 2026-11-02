import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,TransportLost,validate_motion,AUTOMATION_DEFAULTS
CFG=json.loads(Path('src/default-config.json').read_text())

class IdentityObservation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name,'device_udid':'a'*20},notify=lambda _:None)
  self.r.wait=Mock();self.r.gesture=Mock();self.r.recover_connection=Mock()
 def events(self):
  return [json.loads(line) for line in (self.r.log_dir/'events.jsonl').read_text().splitlines()]
 def test_ten_or_twenty_acknowledged_swipes_keep_connection_and_alert_once(self):
  r=self.r
  for limit in (10,20):
   r.cfg['identity_observation_swipes']=limit;r.identity_degraded=False
   r.read_reel=Mock(return_value={'key':None})
   initial=len([e for e in self.events() if e['event']=='feature_disabled']) if (r.log_dir/'events.jsonl').exists() else 0
   for _ in range(limit-1):r.swipe('up')
   self.assertFalse(r.identity_warned)
   r.swipe('up');self.assertTrue(r.identity_warned)
   for _ in range(5):r.swipe('up')
   self.assertEqual(r.identity_swipes,limit+5)
   self.assertEqual(len([e for e in self.events() if e['event']=='feature_disabled']),initial+1)
   self.assertFalse(r.feature_state['identity']['enabled'])
  r.recover_connection.assert_not_called()
 def test_polls_and_backwards_swipes_do_not_count_or_restore_identity(self):
  r=self.r;r.observe_identity({'key':None})
  for _ in range(30):r.observe_identity({'key':'a'})
  r.read_reel=Mock(return_value={'key':'b'});r.swipe('down')
  self.assertEqual(r.identity_swipes,0);self.assertTrue(r.identity_degraded)
 def test_restore_requires_distinct_keys_after_forward_swipes_and_missing_resets_proof(self):
  r=self.r;r.observe_identity({'key':None})
  for key in ('a','a',None,'b'):
   r.observe_identity({'key':key},advanced=True);self.assertTrue(r.identity_degraded)
  r.observe_identity({'key':'c'},advanced=True)
  self.assertFalse(r.identity_degraded);self.assertTrue(r.skip_interactions_once)
  self.assertTrue(r.feature_state['identity']['enabled'])
 def test_all_social_entrypoints_block_before_read_click_model_or_reservation(self):
  r=self.r;r.observe_identity({'key':None});r.read_reel=Mock();r.click=Mock()
  post={'key':'known','caption':'AI','nodes':[]}
  before=json.dumps(r.ledger,sort_keys=True)
  r.consider_like(post,True);r._toggle_on(post,'likes','like-button',{'like'})
  r.send_comment(post,'Nice clip');r.ai_comment(post)
  self.assertFalse(r.reserve(post,'comments','Nice clip'))
  self.assertEqual(before,json.dumps(r.ledger,sort_keys=True));r.read_reel.assert_not_called();r.click.assert_not_called()
 def test_missing_identity_main_loop_browses_without_watch_social_search_or_reconnect(self):
  r=self.r;r.connect=Mock();r.reels=Mock();r.watch_video=Mock();r.consider_like=Mock();r.search_optional=Mock();r.ai_comment=Mock()
  r.read_reel=Mock(return_value={'key':None,'author':'test','caption':'','nodes':[]})
  def gesture(*args,**kwargs):
   if r.swipes==11:r.stop.set()
  r.gesture.side_effect=gesture
  with patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(Path(self.tmp.name)/'locks')):
   self.assertEqual(r.run(),'stopped')
  self.assertGreaterEqual(r.swipes,10)
  for method in (r.watch_video,r.consider_like,r.search_optional,r.ai_comment,r.recover_connection):method.assert_not_called()
 def test_uncertain_action_never_counts_or_resends_in_observation_mode(self):
  r=self.r
  def gesture(*args,**kwargs):r._gesture_action_attempted=True;raise TransportLost('timed out')
  r.gesture.side_effect=gesture;r.read_reel=Mock(return_value={'key':None})
  with self.assertRaisesRegex(RuntimeError,'有限核验'):r.advance_after_read_timeout({'key':None})
  r.gesture.assert_called_once();self.assertEqual(r.identity_swipes,0);r.recover_connection.assert_not_called()
 def test_limits_are_validated(self):
  for value in (9,21,10.5,True,'10'):
   with self.assertRaises(ValueError):validate_motion({**AUTOMATION_DEFAULTS,'identity_observation_swipes':value})
  for value in (10,20):validate_motion({**AUTOMATION_DEFAULTS,'identity_observation_swipes':value})
 def test_normal_swipe_uncertain_reply_stops_without_count_or_retry(self):
  r=self.r;r.read_reel=Mock(return_value={'key':None})
  def gesture(*args):r._gesture_action_attempted=True;raise TransportLost('timed out')
  r.gesture.side_effect=gesture
  with self.assertRaisesRegex(RuntimeError,'不会重复滑动'):r.swipe('up')
  self.assertEqual(r.swipes,0);self.assertEqual(r.identity_swipes,0)
  r.gesture.assert_called_once();r.recover_connection.assert_not_called()

if __name__=='__main__':unittest.main()
