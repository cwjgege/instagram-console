import sys,unittest,asyncio,json
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()));sys.path.insert(0,str(Path('location_helper').resolve()))
import controller,controller_bridge as bridge
UDID='00008030-000611240AD2802E';BUNDLE='com.igconsole.wda.example.xctrunner'
class RelayValidation(unittest.TestCase):
 def test_only_explicit_device_signed_runner_and_distinct_loopback_ports(self):
  cfg=dict(udid=UDID,bundle=BUNDLE,local_wda=19001,local_mjpeg=19002,remote_wda=18100,remote_mjpeg=18101)
  self.assertEqual(controller.validate(cfg),cfg)
  for delta in ({'udid':'all'},{'bundle':'arbitrary.app'},{'local_wda':19002},{'remote_wda':True},{'remote_mjpeg':-1}):
   with self.assertRaises(ValueError):controller.validate({**cfg,**delta})
 def test_wrong_device_or_bundle_proof_never_queries_or_updates_binding(self):
  for delta in ({'udid':'another'},{'bundle':'wrong'},{'fingerprint':'unknown'}):
   proof=dict(udid=UDID,bundle=BUNDLE,fingerprint='pin');proof.update(delta)
   with patch.object(bridge.cn,'checked') as checked,patch.object(bridge.cn,'save') as save,self.assertRaises(RuntimeError):
    bridge.accept(UDID,{'bundle_id':BUNDLE},proof,'http://127.0.0.1:19001',{},None)
   checked.assert_not_called();save.assert_not_called()
 def test_verified_device_refresh_uses_compare_and_swap(self):
  proof=dict(udid=UDID,bundle=BUNDLE,fingerprint='new');setting={'mode':'wifi','fingerprint':'old'}
  with patch.object(bridge.cn,'checked',return_value={'bundle':BUNDLE,'ios':'26.6'}),patch.object(bridge.cn,'load',return_value=dict(setting)),patch.object(bridge.cn,'save') as save:
   bridge.accept(UDID,{'bundle_id':BUNDLE},proof,'http://127.0.0.1:19001',setting,None)
  self.assertEqual(save.call_args.kwargs['expected_fingerprint'],'old');self.assertEqual(setting['fingerprint'],'new')
 def test_truncated_wda_body_does_not_loop_forever(self):
  class Conn:
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
   async def sendall(self,value):pass
   async def recv_any(self,n):return next(self.values,b'')
  conn=Conn();conn.values=iter([b'HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{'])
  class Rsd:
   async def create_service_connection(self,port):return conn
  with self.assertRaisesRegex(RuntimeError,'Incomplete'):asyncio.run(controller.response(Rsd(),18100,'/status'))

class FallbackSelection(unittest.TestCase):
 def test_only_explicit_xctest_channel_rejection_selects_fallback(self):
  import connections as cn
  def rejected(udid,notify,stop,override,trace):
   trace['controller_channel_refused']=True;raise RuntimeError('XCTest channel rejected')
  with patch.object(cn,'_ensure_wifi',side_effect=rejected),patch.object(cn,'stop_device') as stop,patch.object(bridge,'ensure',return_value='http://127.0.0.1:19001') as fallback:
   self.assertEqual(cn.ensure_wifi(UDID,setting_override={'mode':'wifi'}),'http://127.0.0.1:19001')
   fallback.assert_called_once();stop.assert_called_once_with(UDID)
