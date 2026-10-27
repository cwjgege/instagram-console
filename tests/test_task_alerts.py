import sys,tempfile,json,unittest,threading
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from notifications import TaskAlerts,deliver
class Alerts(unittest.TestCase):
 def test_duplicate_error_is_alerted_once_and_new_run_can_alert(self):
  with tempfile.TemporaryDirectory() as root:
   event=threading.Event();send=Mock(side_effect=lambda *args:event.set());alerts=TaskAlerts(root,'test-phone',send)
   self.assertTrue(alerts.handle(1,{'event':'error','message':'private caption/token'}));self.assertTrue(event.wait(2));event.clear()
   self.assertFalse(alerts.handle(1,{'event':'error'}));self.assertTrue(alerts.handle(2,{'event':'error'}));self.assertTrue(event.wait(2))
   self.assertEqual(send.call_count,2);self.assertNotIn('private',str(send.call_args_list))
   self.assertEqual(len(json.loads(alerts.path.read_text())),2)
 def test_expected_stops_completion_and_retries_are_quiet(self):
  alerts=TaskAlerts('/unused','phone',Mock())
  for event in ('stopped','finished','read_retry','reconnecting','recovered','paused'):
   self.assertFalse(alerts.handle(1,{'event':event}))
 def test_delivery_failure_is_not_a_task_error_and_argv_is_literal(self):
  with patch('notifications.sys.platform','darwin'),patch('notifications.subprocess.run',side_effect=OSError()) as run:
   self.assertFalse(deliver('title','phone','" & malicious()'))
   self.assertEqual(run.call_args.args[0][-1],'" & malicious()')
 def test_unwritable_history_does_not_suppress_notification(self):
  event=threading.Event();alerts=TaskAlerts('/unused','phone',lambda *args:event.set())
  with patch('notifications.Path.mkdir',side_effect=OSError()):self.assertTrue(alerts.handle(1,{'event':'resume_blocked'}))
  self.assertTrue(event.wait(2))
