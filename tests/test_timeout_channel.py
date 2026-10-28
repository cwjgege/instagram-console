import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,Halt,TransportLost
from page_state import PageBlocked

CFG=json.loads(Path('src/default-config.json').read_text())

class TimeoutChannel(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.r=Runner({**CFG,'runtime_dir':self.tmp.name,'device_udid':'a'*20},notify=lambda _:None)
  self.r.path='/session/bound-phone';self.r.wait=Mock()
 def test_probe_reads_phone_not_local_server_health_or_expired_operation_budget(self):
  r=self.r;r.http=Mock(return_value={'width':414,'height':896})
  r.watch_read_deadline=0;r.search_deadline=0;r.search_overall_deadline=0
  self.assertTrue(r.control_available())
  r.http.assert_called_once_with('GET','/session/bound-phone/window/rect',None,timeout=15)
 def test_slow_first_probe_can_recover_without_rebuilding_session(self):
  r=self.r;r.http=Mock(side_effect=[TimeoutError('slow'),{'bundleId':'com.burbn.instagram'}]);r.connect=Mock()
  self.assertTrue(r.control_available());self.assertEqual(r.http.call_count,2);r.connect.assert_not_called()
 def test_unreachable_or_empty_response_does_not_claim_healthy(self):
  for responses in ([TimeoutError('slow'),TimeoutError('slow')],[{},{}]):
   self.r.http=Mock(side_effect=responses)
   self.assertFalse(self.r.control_available());self.assertEqual(self.r.http.call_count,2)
 def test_manual_stop_is_not_swallowed(self):
  self.r.stop.set();self.r.http=Mock()
  with self.assertRaises(Halt):self.r.control_available()
  self.r.http.assert_not_called()
 def test_progress_skip_advances_once_without_interaction_or_search(self):
  r=self.r;r.connect=Mock();r.reels=Mock();r.consider_like=Mock();r.toggle_on=Mock();r.comment=Mock();r.search_optional=Mock()
  r.rng=Mock();r.rng.uniform.return_value=0;r.rng.random.return_value=0
  r.read_reel=Mock(return_value={'key':'clip','author':'test','caption':'AI','nodes':[]})
  r.watch_video=Mock(return_value=True)
  def advance(post):self.assertEqual(post['key'],'clip');r.swipes+=1;r.stop.set()
  r.advance_after_read_timeout=Mock(side_effect=advance)
  with patch.dict('os.environ',IG_CONSOLE_LOCK_DIR=str(Path(self.tmp.name)/'locks')):
   self.assertEqual(r.run(),'stopped')
  r.advance_after_read_timeout.assert_called_once();r.consider_like.assert_not_called();r.toggle_on.assert_not_called()
  r.comment.assert_not_called();r.search_optional.assert_not_called()

 def test_progress_timeout_swipes_without_prereading_and_verifies_new_video(self):
  r=self.r;r.gesture=Mock();r.read_reel=Mock(return_value={'key':'next'});r.control_available=Mock();r.recover_connection=Mock()
  r.advance_after_read_timeout({'key':'previous'})
  r.gesture.assert_called_once_with('up',allow_cached_rect=True)
  self.assertEqual(r.swipes,1);self.assertTrue(r.skip_interactions_once)
  self.assertEqual(r.last_swipe_post['key'],'next');r.control_available.assert_not_called();r.recover_connection.assert_not_called()

 def test_lost_action_reply_is_not_resent_even_when_first_read_is_old_video(self):
  r=self.r
  def gesture(*args,**kwargs):r._gesture_action_attempted=True;raise TransportLost('reply timed out')
  r.gesture=Mock(side_effect=gesture);r.read_reel=Mock(side_effect=[{'key':'old'},{'key':'new'}]);r.recover_connection=Mock()
  r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();self.assertEqual(r.read_reel.call_count,2);self.assertEqual(r.swipes,1)
  r.recover_connection.assert_not_called()

 def test_page_timeout_after_swipe_waits_for_readback_without_resending(self):
  r=self.r;r.gesture=Mock();r.read_reel=Mock(side_effect=[TransportLost('slow'),{'key':'new'}]);r.wait_for_page_response=Mock(return_value=object())
  r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();r.wait_for_page_response.assert_called_once();self.assertEqual(r.swipes,1)

 def test_uncertain_swipe_stays_pending_until_manual_stop_not_blind_retry(self):
  r=self.r
  def gesture(*args,**kwargs):r._gesture_action_attempted=True;raise TransportLost('reply timed out')
  r.gesture=Mock(side_effect=gesture);r.read_reel=Mock(side_effect=[{'key':'old'},Halt('stop')])
  with self.assertRaises(Halt):r.advance_after_read_timeout({'key':'old'})
  r.gesture.assert_called_once();self.assertEqual(r.swipes,0)

 def test_slow_page_with_responsive_control_waits_without_reconnect(self):
  r=self.r;r.snapshot=Mock(side_effect=[TransportLost('slow'),TransportLost('slow'),object()]);r.observe=Mock()
  r.control_available=Mock(return_value=True);r.recover_connection=Mock()
  self.assertIsNotNone(r.wait_for_page_response());self.assertEqual(r.control_available.call_count,2);r.recover_connection.assert_not_called()

 def test_reconnect_requires_repeated_page_and_independent_check_failures(self):
  r=self.r;r.snapshot=Mock(side_effect=TransportLost('lost'));r.control_available=Mock(return_value=False);r.recover_connection=Mock()
  self.assertIsNone(r.wait_for_page_response());self.assertEqual(r.snapshot.call_count,2)
  self.assertEqual(r.control_available.call_count,2);r.recover_connection.assert_called_once()

 def test_successful_control_response_resets_failure_streak(self):
  r=self.r;r.snapshot=Mock(side_effect=[TransportLost('slow')]*4+[object()]);r.observe=Mock()
  r.control_available=Mock(side_effect=[False,True,False,True]);r.recover_connection=Mock()
  r.wait_for_page_response();r.recover_connection.assert_not_called()

 def test_security_page_is_not_ignored_while_waiting(self):
  r=self.r;r.snapshot=Mock(side_effect=PageBlocked('account verification'));r.control_available=Mock()
  with self.assertRaises(PageBlocked):r.wait_for_page_response()
  r.control_available.assert_not_called()

 def test_degraded_gesture_reuses_dimensions_only_in_same_session(self):
  r=self.r;r._gesture_rect_cache=(r.path,{'width':414,'height':896});r.command=Mock()
  r.gesture('up',allow_cached_rect=True)
  self.assertEqual(r.command.call_count,1);self.assertEqual(r.command.call_args.args[:2],('POST','/actions'))
  r.path='/session/new';r.command=Mock(side_effect=[{'width':375,'height':812},None])
  r.gesture('up',allow_cached_rect=True)
  self.assertEqual(r.command.call_args_list[0].args,('GET','/window/rect'))
