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
 async def test_existing_wda_is_verified_forwarded_and_released_without_custom_xctest(self):
  routes=[]
  async def http(reader,writer):
   raw=await reader.readuntil(b'\r\n\r\n');route=raw.split(b' ')[1].decode();routes.append(route)
   value={'ready':True,'build':{'productBundleIdentifier':BUNDLE},'os':{'version':'26.6'}} if route=='/status' else {'uuid':'paired-fixture-pin'}
   data=json.dumps({'value':value}).encode();writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data);await writer.drain();writer.close();await writer.wait_closed()
  remote=await asyncio.start_server(http,'127.0.0.1',0);remote_port=remote.sockets[0].getsockname()[1]
  local_wda,local_mjpeg,remote_mjpeg=free_ports(3)
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
