import asyncio,io,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path('src').resolve()))
sys.path.insert(0,str(Path('.').resolve()))
import location_manager as lm,device_manager as dm
from location_helper import worker
ONE='00008030-0011223344556677';TWO='00008030-008899AABBCCDDEE'

class Coordinates(unittest.TestCase):
 def test_ranges_and_non_finite(self):
  for a,b in [('nan',1),(1,'inf'),(91,0),(0,-181),(True,0),('',0)]:
   with self.subTest(a=a,b=b),self.assertRaises(ValueError):lm.coordinates(a,b)
  self.assertEqual(lm.coordinates('40.7128','-74.0060'),lm.NEW_YORK)
 def test_alias_rejected(self):
  with self.assertRaises(ValueError):lm.validate_target('a'*32)
  with patch('connections.load',return_value={'identifier_kind':'wda'}),self.assertRaises(ValueError):lm.validate_target(ONE)

class Backend:
 def __init__(self,wrong=False):self.calls=[];self.wrong=wrong
 async def __aenter__(self):return self
 async def __aexit__(self,*args):pass
 async def connect(self,udid):self.calls.append(('connect',udid))
 async def verify(self,udid):
  self.calls.append(('verify',udid))
  if self.wrong:raise RuntimeError('wrong device')
 async def set(self,lat,lon):self.calls.append(('set',lat,lon))
 async def clear(self):self.calls.append(('clear',))

class Protocol(unittest.IsolatedAsyncioTestCase):
 async def run_requests(self,requests,backend):
  r=asyncio.StreamReader()
  r.feed_data((''.join(json.dumps(x)+'\n' for x in requests)).encode());r.feed_eof()
  with patch.object(worker,'version',return_value=worker.PIN),patch('sys.stdout',new_callable=io.StringIO) as out:
   await worker.serve(r,backend)
   return [json.loads(x) for x in out.getvalue().splitlines()]
 async def test_set_then_clear_verifies_identity_before_each_write(self):
  b=Backend();events=await self.run_requests([dict(action='set',udid=ONE,latitude=40.7128,longitude=-74.006),dict(action='clear',udid=ONE)],b)
  self.assertEqual([x['event'] for x in events],['applied','clear_sent'])
  self.assertEqual([x[0] for x in b.calls],['connect','verify','set','verify','clear'])
 async def test_identity_failure_does_not_write(self):
  b=Backend(True)
  with self.assertRaises(RuntimeError):await self.run_requests([dict(action='set',udid=ONE,latitude=1,longitude=2)],b)
  self.assertFalse(any(x[0]=='set' for x in b.calls))
 async def test_changing_target_rejected_before_second_write(self):
  b=Backend()
  with self.assertRaises(ValueError):await self.run_requests([dict(action='set',udid=ONE,latitude=1,longitude=2),dict(action='clear',udid=TWO)],b)
  self.assertFalse(any(x[0]=='clear' for x in b.calls))
 async def test_eof_attempts_restore(self):
  events=await self.run_requests([dict(action='set',udid=ONE,latitude=1,longitude=2)],Backend())
  self.assertEqual(events[-1]['event'],'clear_sent')
 async def test_invalid_coordinate_never_connects(self):
  b=Backend()
  with self.assertRaises(ValueError):await self.run_requests([dict(action='set',udid=ONE,latitude=100,longitude=2)],b)
  self.assertEqual(b.calls,[])

STUB='''import json,sys
for line in sys.stdin:
 r=json.loads(line)
 if r.get('latitude')==88:
  print(json.dumps({'event':'applied','udid':'f'*40,'latitude':1,'longitude':2}),flush=True);break
 if r.get('latitude')==89:
  print(json.dumps({'event':'error','error':'device offline'}),flush=True);break
 if r['action']=='clear':
  print(json.dumps({'event':'clear_sent','udid':r['udid']}),flush=True);break
 print(json.dumps({'event':'applied','udid':r['udid'],'latitude':r['latitude'],'longitude':r['longitude']}),flush=True)
'''
class Processes(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name);self.script=self.path/'worker.py';self.script.write_text(STUB)
  self.data=patch.object(dm,'DATA',self.path);self.data.start()
  self.cmd=patch.object(lm,'worker_command',return_value=[sys.executable,str(self.script)]);self.cmd.start()
  self.target=patch('connections.load',return_value={});self.target.start();self.m=lm.LocationManager()
 def tearDown(self):
  self.m.shutdown();self.wait(lambda:not self.m.sessions);self.target.stop();self.cmd.stop();self.data.stop();self.tmp.cleanup()
 def wait(self,predicate):
  end=time.monotonic()+5
  while time.monotonic()<end:
   if predicate():return
   time.sleep(.01)
  self.fail('process state did not settle')
 def test_two_phones_are_independent_and_clear_does_not_claim_verified_gps(self):
  self.m.apply(ONE,*lm.NEW_YORK);self.m.apply(TWO,1,2)
  self.wait(lambda:all(self.m.snapshot(x)['phase']=='applied' for x in (ONE,TWO)))
  self.assertNotEqual(self.m.sessions[ONE]['process'].pid,self.m.sessions[TWO]['process'].pid)
  self.m.clear(ONE);self.wait(lambda:ONE not in self.m.sessions)
  self.assertEqual(self.m.snapshot(ONE)['phase'],'clear_sent');self.assertEqual(self.m.snapshot(TWO)['phase'],'applied')
  self.assertIn('手机地图中确认',self.m.snapshot(ONE)['message'])
 def test_foreign_identity_cannot_report_success(self):
  self.m.apply(ONE,88,2);self.wait(lambda:ONE not in self.m.sessions)
  self.assertEqual(self.m.snapshot(ONE)['phase'],'unknown')
 def test_error_never_becomes_success(self):
  self.m.apply(ONE,89,2);self.wait(lambda:ONE not in self.m.sessions)
  self.assertEqual(self.m.snapshot(ONE)['phase'],'unknown')
 def test_saved_request_not_reported_as_live_after_restart(self):
  self.m.apply(ONE,1,2);self.wait(lambda:self.m.snapshot(ONE)['phase']=='applied')
  fresh=lm.LocationManager();self.assertEqual(fresh.snapshot(ONE)['phase'],'idle')
  self.assertIn('尚未确认',fresh.snapshot(ONE)['message'])
 def test_shutdown_restores_owned_sessions(self):
  self.m.apply(ONE,1,2);self.m.apply(TWO,3,4)
  self.wait(lambda:all(self.m.snapshot(x)['phase']=='applied' for x in (ONE,TWO)))
  self.m.shutdown();self.wait(lambda:not self.m.sessions)
  for x in (ONE,TWO):self.assertEqual(self.m.snapshot(x)['phase'],'clear_sent')
  with self.assertRaises(RuntimeError):self.m.apply(ONE,1,2)
 def test_expired_request_terminated_and_not_reported_applied(self):
  self.m.apply(ONE,1,2);self.wait(lambda:self.m.snapshot(ONE)['phase']=='applied')
  with self.m.lock:
   session=self.m.sessions[ONE];session['deadline']=time.monotonic()-1;self.m._state(ONE,'busy','waiting')
   self.m._timeout(ONE,session,session['deadline'])
  self.wait(lambda:ONE not in self.m.sessions)
  self.assertEqual(self.m.snapshot(ONE)['phase'],'unknown')
