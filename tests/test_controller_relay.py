"""Real local HTTP/TCP fixtures; never enumerate or connect a physical iPhone."""
import asyncio,json,socket,sys,types,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path.insert(0,str(Path('location_helper').resolve()))
import controller
UDID='00008030-0011223344556677';BUNDLE='com.igconsole.wda.fixture.xctrunner'
def free_ports(count):
 sockets=[]
 try:
  for _ in range(count):
   sock=socket.socket();sock.bind(('127.0.0.1',0));sockets.append(sock)
  return [s.getsockname()[1] for s in sockets]
 finally:
  for sock in sockets:sock.close()
class RelayRoundTrip(unittest.IsolatedAsyncioTestCase):
 async def round_trip(self,dynamic=False):
  routes=[]
  async def http(reader,writer):
   raw=await reader.readuntil(b'\r\n\r\n');route=raw.split(b' ')[1].decode();routes.append(route)
   value={'ready':True,'build':{'productBundleIdentifier':BUNDLE},'os':{'version':'26.6'}} if route=='/status' else {'uuid':'paired-fixture-pin'}
   data=json.dumps({'value':value}).encode();writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data);await writer.drain();writer.close();await writer.wait_closed()
  remote=await asyncio.start_server(http,'127.0.0.1',0);remote_port=remote.sockets[0].getsockname()[1]
  local_wda,local_mjpeg,remote_mjpeg=free_ports(3)
  blocker=None
  if dynamic:
   blocker=socket.socket();blocker.bind(('127.0.0.1',local_wda));blocker.listen();old_port=local_wda
   local_wda=local_mjpeg=0
  cfg=dict(udid=UDID,bundle=BUNDLE,local_wda=local_wda,local_mjpeg=local_mjpeg,remote_wda=remote_port,remote_mjpeg=remote_mjpeg)
  class Connection:
   async def __aenter__(self):self.reader,self.writer=await asyncio.open_connection('127.0.0.1',remote_port);return self
   async def __aexit__(self,*args):self.writer.close();await self.writer.wait_closed()
   async def sendall(self,value):self.writer.write(value);await self.writer.drain()
   async def recv_any(self,n):return await self.reader.read(n)
  class Rsd:
   udid=UDID
   async def create_service_connection(self,port):
    if port!=remote_port:raise OSError('unused fixture MJPEG')
    return Connection()
  class Tunnel:
   def __init__(self,**kwargs):self.kwargs=kwargs
   async def __aenter__(self):return Rsd()
   async def __aexit__(self,*args):pass
  module=types.ModuleType('pymobiledevice3.remote.userspace_tunnel');module.UserspaceRsdTunnel=Tunnel
  ready=asyncio.Event();proof={}
  def emit(event,**data):
   if event=='controller_ready':proof.update(data);ready.set()
  task=None
  try:
   with patch.dict(sys.modules,{'pymobiledevice3.remote.userspace_tunnel':module}),patch.object(controller,'launch_standard',new=AsyncMock()) as launch:
    task=asyncio.create_task(controller.serve(cfg,emit));await asyncio.wait_for(ready.wait(),3)
    if dynamic:
     local_wda=proof['local_wda'];local_mjpeg=proof['local_mjpeg']
     self.assertNotEqual(local_wda,local_mjpeg);self.assertNotIn(old_port,(local_wda,local_mjpeg));self.assertEqual(blocker.getsockname()[1],old_port)
    reader,writer=await asyncio.open_connection('127.0.0.1',local_wda)
    writer.write(b'GET /wda/device/info HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n');await writer.drain()
    data=await asyncio.wait_for(reader.read(),3);writer.close();await writer.wait_closed()
    self.assertIn(b'paired-fixture-pin',data);self.assertEqual(proof['udid'],UDID);self.assertEqual(proof['bundle'],BUNDLE);launch.assert_not_called()
    task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    with self.assertRaises(OSError):await asyncio.open_connection('127.0.0.1',local_wda)
   self.assertEqual(set(routes),{'/status','/wda/device/info'})
  finally:
   if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
   remote.close();await remote.wait_closed()
   if blocker:blocker.close()

 async def test_existing_wda_is_verified_forwarded_and_released_without_custom_xctest(self):
  await self.round_trip()
 async def test_existing_usb_port_can_remain_occupied_while_os_assigns_wireless_ports(self):
  await self.round_trip(dynamic=True)
 async def test_two_wireless_relays_allocate_and_release_ports_independently(self):
  # Each production relay owns a separate helper process and isolated tunnel.
  # Use separate fixture processes too, so mocked RSD providers cannot overwrite one another.
  script="import sys,unittest;sys.path.insert(0,'tests');from test_controller_relay import RelayRoundTrip;result=unittest.TextTestRunner().run(unittest.TestSuite([RelayRoundTrip('test_existing_usb_port_can_remain_occupied_while_os_assigns_wireless_ports')]));sys.exit(not result.wasSuccessful())"
  children=[await asyncio.create_subprocess_exec(sys.executable,'-c',script,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT) for _ in range(2)]
  outputs=await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in children)),15)
  for process,output in zip(children,outputs):self.assertEqual(process.returncode,0,output[0].decode())
