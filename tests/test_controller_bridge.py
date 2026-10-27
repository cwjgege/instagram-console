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
  import tempfile,device_manager as dm
  with tempfile.TemporaryDirectory() as directory,patch.object(dm,'DATA',Path(directory)),patch.object(cn,'_ensure_wifi',side_effect=rejected),patch.object(cn,'stop_device') as stop,patch.object(bridge,'ensure',return_value='http://127.0.0.1:19001') as fallback:
   self.assertEqual(cn.ensure_wifi(UDID,setting_override={'mode':'wifi'}),'http://127.0.0.1:19001')
   fallback.assert_called_once();stop.assert_called_once_with(UDID)

class IdentityUnavailableFallback(unittest.TestCase):
 def failure(self,error,transport='localNetwork'):
  def fail(udid,notify,stop,override,trace):
   trace.update(stage='existing_endpoint',transport=transport);raise error
  return fail
 def test_unavailable_paired_proof_on_wifi_selects_verified_fallback(self):
  import tempfile
  import connections as cn,device_manager as dm
  with tempfile.TemporaryDirectory() as folder,patch.object(dm,'DATA',Path(folder)),patch.object(cn,'_ensure_wifi',side_effect=self.failure(cn.IdentityProofUnavailable('usbmux unavailable'))),patch.object(cn,'stop_device'),patch.object(bridge,'ensure',return_value='http://127.0.0.1:19001') as fallback:
   self.assertEqual(cn.ensure_wifi(UDID,setting_override={'mode':'wifi','fingerprint':'old'}),'http://127.0.0.1:19001')
   self.assertEqual(fallback.call_args.args[1]['fingerprint'],'old')
   attempt=json.loads((dm.device_dir(UDID)/'connection-attempt.json').read_text());self.assertEqual(attempt['fallback_reason'],'paired_identity_unavailable')
 def test_actual_identity_mismatch_and_unknown_transport_do_not_select_fallback(self):
  import tempfile
  import connections as cn,device_manager as dm
  for error,transport in ((cn.FingerprintMismatch('wrong device'),'localNetwork'),(cn.IdentityProofUnavailable('no proof'),'wired'),(RuntimeError('ordinary failure'),'localNetwork')):
   with tempfile.TemporaryDirectory() as folder,patch.object(dm,'DATA',Path(folder)),patch.object(cn,'_ensure_wifi',side_effect=self.failure(error,transport)),patch.object(cn,'stop_device'),patch.object(bridge,'ensure') as fallback,self.assertRaises(RuntimeError):cn.ensure_wifi(UDID)
   fallback.assert_not_called()
 def test_failed_fallback_preserves_existing_binding_and_both_errors(self):
  import tempfile
  import connections as cn,device_manager as dm
  with tempfile.TemporaryDirectory() as folder,patch.object(dm,'DATA',Path(folder)):
   old={'mode':'wifi','fingerprint':'old'};cn.save(UDID,old)
   with patch.object(cn,'_ensure_wifi',side_effect=self.failure(cn.IdentityProofUnavailable('paired proof unavailable'))),patch.object(cn,'stop_device'),patch.object(bridge,'ensure',side_effect=RuntimeError('signed proof failed')),self.assertRaisesRegex(RuntimeError,'signed proof failed'):cn.ensure_wifi(UDID)
   self.assertEqual(cn.load(UDID),old)
 def test_cancelled_fallback_never_saves_or_retries(self):
  import connections as cn
  from runner import Halt
  with patch.object(cn,'_ensure_wifi',side_effect=self.failure(cn.IdentityProofUnavailable('unavailable'))),patch.object(cn,'stop_device'),patch.object(bridge,'ensure',side_effect=Halt('stop')),self.assertRaises(Halt):cn.ensure_wifi(UDID,setting_override={'mode':'wifi'})

class StandardStartup(unittest.IsolatedAsyncioTestCase):
 def cfg(self):return dict(udid=UDID,bundle=BUNDLE,local_wda=19001,local_mjpeg=19002,remote_wda=8100,remote_mjpeg=19002)
 async def test_existing_signed_wda_is_reused_without_any_xctest_launch(self):
  from unittest.mock import AsyncMock
  proof=({'build':{'productBundleIdentifier':BUNDLE}},{'uuid':'new'})
  with patch.object(controller,'identity',new=AsyncMock(return_value=proof)),patch.object(controller,'launch_standard',new=AsyncMock()) as launch:
   self.assertEqual(await controller.ready(object(),self.cfg(),lambda *a,**k:None),(*proof,None))
   launch.assert_not_called()
 async def test_absent_existing_wda_uses_standard_xcode_and_returns_owned_child(self):
  from unittest.mock import AsyncMock
  child=Mock(returncode=None);proof=({'ready':True},{'uuid':'new'})
  class TunnelConnectError(Exception):pass
  with patch.object(controller,'identity',new=AsyncMock(side_effect=[TunnelConnectError('port closed'),proof])),patch.object(controller,'launch_standard',new=AsyncMock(return_value=child)) as launch:
   self.assertEqual(await controller.ready(object(),self.cfg(),lambda *a,**k:None),(*proof,child));launch.assert_awaited_once()
 async def test_wrong_signed_bundle_never_launches_or_returns_proof(self):
  from unittest.mock import AsyncMock
  with patch.object(controller,'identity',new=AsyncMock(side_effect=controller.IdentityMismatch('another bundle'))),patch.object(controller,'launch_standard',new=AsyncMock()) as launch,self.assertRaises(controller.IdentityMismatch):await controller.ready(object(),self.cfg(),lambda *a,**k:None)
  launch.assert_not_called()
 async def test_failed_xcode_child_is_reaped_without_masking_startup_error(self):
  from unittest.mock import AsyncMock
  child=Mock(returncode=65);child.wait=AsyncMock(return_value=65)
  with patch.object(controller,'identity',new=AsyncMock(side_effect=OSError('no WDA'))),patch.object(controller,'launch_standard',new=AsyncMock(return_value=child)),self.assertRaisesRegex(RuntimeError,'65'):await controller.ready(object(),self.cfg(),lambda *a,**k:None)
  child.wait.assert_awaited_once()
 async def test_launch_uses_test_without_building_for_only_selected_device(self):
  import tempfile
  from unittest.mock import AsyncMock
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);executable=root/'Developer/usr/bin/xcodebuild';executable.parent.mkdir(parents=True);executable.touch();spec=root/'wireless.xctestrun';spec.touch()
   cfg={**self.cfg(),'xcodebuild':str(executable),'xctestrun':str(spec)}
   with patch.object(controller.asyncio,'create_subprocess_exec',new=AsyncMock()) as start:
    await controller.launch_standard(cfg)
   args=start.call_args.args;self.assertIn('test-without-building',args);self.assertIn('id='+UDID,args);self.assertNotIn('-allowProvisioningUpdates',args)
 async def test_missing_build_does_not_launch_arbitrary_process(self):
  from unittest.mock import AsyncMock
  with patch.object(controller.asyncio,'create_subprocess_exec',new=AsyncMock()) as start,self.assertRaisesRegex(RuntimeError,'本机签名'):await controller.launch_standard(self.cfg())
  start.assert_not_called()

class StartupDiagnostics(unittest.TestCase):
 def test_legacy_runner_disconnection_is_reported_instead_of_generic_pairing_hint(self):
  import tempfile
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'controller-wireless.log';path.write_text('warning\npymobiledevice3.exceptions.ConnectionTerminatedError: The test runner disconnected\n[PYI:ERROR] Failed to execute script\n')
   error=bridge.startup_failure(path,1)
  self.assertIn('ConnectionTerminatedError',str(error));self.assertIn('不代表开发者模式未开启',str(error))
 def test_structured_helper_error_is_included_in_connection_failure(self):
  import tempfile
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'controller-wireless.log';path.write_text(json.dumps({'event':'error','error':'RuntimeError: 标准 Xcode 启动提前结束，退出码 65'}))
   self.assertIn('退出码 65',str(bridge.startup_failure(path,1)))

class DynamicRelayPorts(unittest.TestCase):
 def test_only_valid_actual_ports_from_this_attempt_are_accepted(self):
  request={'relay_protocol':2,'attempt_id':'new-attempt'}
  proof={'attempt_id':'new-attempt','local_wda':51001,'local_mjpeg':51002}
  self.assertEqual(bridge.relay_endpoints(proof,request),('http://127.0.0.1:51001','http://127.0.0.1:51002'))
  for change in ({'attempt_id':'old'},{'local_wda':0},{'local_wda':True},{'local_wda':65536},{'local_mjpeg':51001}):
   with self.subTest(change=change),self.assertRaises(RuntimeError):bridge.relay_endpoints({**proof,**change},request)
  with self.assertRaises(RuntimeError):bridge.relay_endpoints(proof,{'relay_protocol':1,'attempt_id':'new-attempt'})
 def test_bridge_requests_os_ports_and_persists_actual_urls_without_using_usb_endpoints(self):
  import tempfile,device_manager as dm,connections as cn
  with tempfile.TemporaryDirectory() as folder,patch.object(dm,'DATA',Path(folder)),patch.object(cn,'_PROCESSES',{}):
   base=dm.device_dir(UDID);base.mkdir(parents=True)
   record={'udid':UDID,'bundle_id':BUNDLE};proof={}
   def launch(*args,**kwargs):
    cfg=json.loads((base/'controller-request.json').read_text())
    self.assertEqual((cfg['local_wda'],cfg['local_mjpeg']),(0,0));self.assertEqual(cfg['relay_protocol'],2)
    proof.update(event='controller_ready',udid=UDID,bundle=BUNDLE,fingerprint='new',ios='26.6',local_wda=51001,local_mjpeg=51002,attempt_id=cfg['attempt_id'])
    kwargs['stdout'].write(b'"Xcode diagnostic string"\n[]\n')
    kwargs['stdout'].write((json.dumps(proof)+'\n').encode());kwargs['stdout'].flush()
    return Mock(pid=12345,returncode=None)
   def checked(url,pin):self.assertEqual(url,'http://127.0.0.1:51001');return dict(bundle=BUNDLE,ios='26.6',fingerprint=pin,url=url)
   with patch.object(dm,'signing_record',return_value=record),patch.object(dm,'expiry_state',return_value=('valid',24)),patch.object(cn,'derived_path',return_value=base),patch.object(cn,'prepare_wireless_xctestrun',return_value=base/'fixture.xctestrun'),patch.object(dm,'xcode_directory',return_value=Path('/fixture')),patch.object(bridge,'worker_command',return_value=['fixture-helper']),patch.object(bridge.subprocess,'Popen',side_effect=launch),patch.object(cn,'checked',side_effect=checked),patch('runtime_manager.endpoints',side_effect=AssertionError('USB port allocation must remain separate')):
    self.assertEqual(bridge.ensure(UDID,{'mode':'wifi'}),'http://127.0.0.1:51001')
   runtime=json.loads((base/'network-runtime.json').read_text());self.assertEqual(runtime['preview_url'],'http://127.0.0.1:51002')
   saved=json.loads((base/'controller-proof.json').read_text());self.assertEqual(saved,proof)
