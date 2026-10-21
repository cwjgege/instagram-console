import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
import connections as cn,device_manager as dm
from runner import Runner

class Connections(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.scope=patch.object(dm,'DATA',self.root);self.scope.start();self.udid='a'*40
 def tearDown(self):self.scope.stop();self.tmp.cleanup()
 def test_existing_profiles_default_to_usb(self):self.assertEqual(cn.load(self.udid),{'mode':'usb'})
 def test_corrupt_config_does_not_silently_revert_to_usb(self):
  p=dm.device_dir(self.udid)/'connection.json';p.parent.mkdir(parents=True);p.write_text('bad')
  with self.assertRaisesRegex(ValueError,'无法读取'):cn.load(self.udid)
 def test_only_private_network_urls_allowed(self):
  for url in ['http://192.168.1.2:8100','http://100.64.1.2:8100','http://[fd00::1]:8100','https://phone.example.ts.net:8100']:
   self.assertEqual(cn.validate_url(url),url)
  for url in ['http://8.8.8.8:8100','http://example.com:8100','file:///etc/passwd','http://user:password@192.168.1.2:8100','http://192.168.1.2:8100/status','http://192.168.1.2:8100?secret=x','http://0.0.0.0:8100','http://224.0.0.1:8100','http://[::]:8100']:
   with self.subTest(url=url),self.assertRaises(ValueError):cn.validate_url(url)
 def test_refuses_missing_or_changed_device_fingerprint(self):
  with patch.object(cn,'inspect',return_value={'fingerprint':'one'}):
   for pin in [None,'other']:
    with self.assertRaisesRegex(RuntimeError,'指纹'):cn.checked('http://100.64.1.2:8100',pin)
   self.assertEqual(cn.checked('http://100.64.1.2:8100','one')['fingerprint'],'one')
 def test_remote_does_not_discover_or_start_local_test_runner(self):
  setting={'mode':'remote','wda_url':'http://100.64.1.2:8100','fingerprint':'one'}
  with patch.object(cn,'load',return_value=setting),patch.object(cn,'checked',return_value={'fingerprint':'one'}),patch.object(dm,'coredevice') as discover,patch.object(cn.subprocess,'Popen') as launch:
   self.assertEqual(cn.ensure_wifi(self.udid),setting['wda_url']);discover.assert_not_called();launch.assert_not_called()
 def test_network_payload_never_requests_build_or_usb_port_forward(self):
  caps=cn.network_payload({'device_udid':self.udid,'device_ios':'18.1'},'http://100.64.1.2:8100')['capabilities']['alwaysMatch']
  self.assertEqual(caps['appium:platformVersion'],'18.1');self.assertTrue(caps['appium:skipLogCapture']);self.assertNotIn('appium:mjpegServerPort',caps);self.assertNotIn('appium:derivedDataPath',caps)
 def test_network_discovery_does_not_auto_enroll_unselected_phones(self):
  phone={'udid':self.udid,'network':True,'paired':True,'developer':True}
  with patch.object(cn,'discover',return_value=[phone]):self.assertEqual(cn.fleet_devices([]),[])
 def test_usb_profile_does_not_silently_use_network(self):
  phone={'udid':self.udid,'network':True,'paired':True,'developer':True}
  with patch.object(cn,'discover',return_value=[phone]):self.assertEqual(cn.fleet_devices([{'device_udid':self.udid}]),[])
 def test_explicit_wifi_profile_survives_unplugged_usb(self):
  phone={'udid':self.udid,'network':True,'paired':True,'developer':True}
  with patch.object(cn,'discover',return_value=[phone]),patch.object(cn,'load',return_value={'mode':'wifi'}):self.assertEqual(cn.fleet_devices([{'device_udid':self.udid}]),[phone])
 def test_unavailable_remote_does_not_hide_healthy_usb_peer(self):
  phone={'udid':'b'*40,'paired':True,'developer':True}
  with patch.object(cn,'discover',return_value=[phone]),patch.object(cn,'load',side_effect=lambda u:{'mode':'remote','wda_url':'http://100.64.1.2:8100','fingerprint':'pin'} if u==self.udid else {'mode':'usb'}),patch.object(cn,'checked',side_effect=OSError('offline')):
   found=cn.fleet_devices([{'device_udid':self.udid},{'device_udid':'b'*40}]);self.assertEqual(found[-1],phone);self.assertFalse(found[0]['paired']);self.assertIn('offline',found[0]['connection_error'])
 def test_network_preview_uses_network_host(self):
  import preview
  with patch.object(cn,'load',return_value={'mode':'remote','preview_url':'http://100.64.1.2:9100'}),patch.object(dm,'prepare_vendor',return_value=Path('/vendor')):
   _,r=preview.stream_request(self.udid);self.assertEqual(r['host'],'100.64.1.2');self.assertEqual(r['port'],9100)
 def test_missing_preview_does_not_guess_or_use_usb(self):
  import preview
  with patch.object(cn,'load',return_value={'mode':'remote'}),self.assertRaisesRegex(RuntimeError,'预览未配置'):preview.stream_request(self.udid)
 def test_network_session_skips_usb_wait_and_legacy_start(self):
  import runner,runtime_manager as rm
  cfg=json.loads(Path('src/default-config.json').read_text());cfg.update(device_udid=self.udid,device_ios='18.1',managed_device=True,runtime_dir=str(self.root))
  r=Runner(cfg);r.wait_for_usb=Mock(side_effect=AssertionError('USB gate invoked'));r.http=Mock(side_effect=[{}, {'sessionId':'new','capabilities':{'udid':self.udid}}]);r.log=Mock()
  with patch.object(cn,'load',return_value={'mode':'remote'}),patch.object(cn,'ensure_wifi',return_value='http://100.64.1.2:8100'),patch.object(rm,'ensure_server',return_value={'url':'http://127.0.0.1:19900/igconsole'}),patch.object(rm,'endpoints',return_value={'wda':19001,'mjpeg':19002}),patch.object(dm,'prepare_vendor'),patch.object(dm,'child_env'),patch.object(dm,'signing_record',return_value=None):
   r.connect();r.wait_for_usb.assert_not_called();caps=r.http.call_args.args[2]['capabilities']['alwaysMatch'];self.assertEqual(caps['appium:webDriverAgentUrl'],'http://100.64.1.2:8100')
 def test_precise_trust_error_precedes_socket_noise(self):
  from setup_errors import classify_setup_error
  issue=classify_setup_error(RuntimeError('code 65'),'Invalid trust settings. Restore system default trust settings; socket hang up')
  self.assertEqual(issue.code,'certificate_trust')
 def test_missing_xcode_destination_has_specific_error(self):
  from setup_errors import classify_setup_error
  self.assertEqual(classify_setup_error(RuntimeError('Unable to find a device matching the provided destination specifier:')).code,'xcode_device_missing')

class LocalWirelessStartup(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.scope=patch.object(dm,'DATA',self.root);self.scope.start();self.udid='c'*40
  self.runtime=dm.device_dir(self.udid);self.products=self.runtime/'DerivedData/Build/Products';self.products.mkdir(parents=True)
  import plistlib
  (self.products/'Runner.xctestrun').write_bytes(plistlib.dumps({'Runner':{'TestBundlePath':'bundle','EnvironmentVariables':{'USE_PORT':'18120','MJPEG_SERVER_PORT':'18121'}}}))
  dm.write_json(self.runtime/'signing.json',{'expires_at':'2030-01-01T00:00:00+00:00','controller':'modern','bundle_id':'com.igconsole.test.xctrunner'})
 def tearDown(self):cn._PROCESSES.clear();self.scope.stop();self.tmp.cleanup()
 def details(self,ip):return {'hardwareProperties':{'udid':self.udid},'connectionProperties':{'pairingState':'paired','tunnelIPAddress':ip},'deviceProperties':{'developerModeStatus':'enabled','osVersionNumber':'18.1'}}
 def test_starts_preinstalled_runner_and_honors_compiled_ports(self):
  process=Mock();process.poll.return_value=None
  with patch.object(dm,'coredevice',side_effect=[self.details('fd00::1'),AssertionError('concurrent CoreDevice query')]),patch.object(cn,'inspect',side_effect=[OSError('not ready'),{'fingerprint':'pin','name':'Test','ios':'18.1','bundle':'com.igconsole.test.xctrunner'}]),patch.object(cn.subprocess,'Popen',return_value=process) as launch,patch.object(dm,'child_env',return_value={}),patch.object(dm,'prepare_vendor',return_value=Path('/vendor')),patch.object(dm,'xcode_directory',return_value=Path('/Xcode')):
   url=cn.ensure_wifi(self.udid,setting_override={'mode':'wifi'});self.assertEqual(url,'http://[fd00::1]:18120')
   args=launch.call_args.args[0];self.assertIn('devicectl',args[0]);self.assertIn('com.igconsole.test.xctrunner',args);self.assertIn('--environment-variables',args);self.assertNotIn('test-without-building',args);self.assertEqual(launch.call_args.kwargs['stdin'],cn.subprocess.DEVNULL)
   runtime=json.loads((self.runtime/'network-runtime.json').read_text());self.assertEqual(runtime['preview_url'],'http://[fd00::1]:18121')
 def test_resume_checks_cached_lan_address_before_starting_xctest(self):
  url='http://192.168.1.4:18120';dm.write_json(self.runtime/'network-runtime.json',{'url':url,'preview_url':'http://192.168.1.4:18121'})
  with patch.object(dm,'coredevice',return_value=self.details('fd00::1')),patch.object(cn,'inspect',return_value={'url':url,'fingerprint':'pin','ios':'18.1'}) as inspect,patch.object(cn.subprocess,'Popen') as launch:
   self.assertEqual(cn.ensure_wifi(self.udid,setting_override={'mode':'wifi','fingerprint':'pin'}),url)
   inspect.assert_called_once_with(url);launch.assert_not_called()
 def test_bound_fingerprint_mismatch_stops_without_launching_other_runner(self):
  with patch.object(dm,'coredevice',return_value=self.details('fd00::1')),patch.object(cn,'inspect',return_value={'fingerprint':'other'}),patch.object(cn.subprocess,'run',return_value=Mock(returncode=0,stdout='')),patch.object(cn.subprocess,'Popen') as launch:
   with self.assertRaisesRegex(RuntimeError,'指纹'):cn.ensure_wifi(self.udid,setting_override={'mode':'wifi','fingerprint':'pin'})
   launch.assert_not_called()
 def test_same_wda_cannot_be_bound_to_two_device_profiles(self):
  cn.save(self.udid,{'mode':'wifi','fingerprint':'one'})
  with self.assertRaisesRegex(RuntimeError,'已经绑定'):cn.save('d'*40,{'mode':'wifi','fingerprint':'one'})
 def test_network_mode_requires_verified_binding(self):
  with self.assertRaisesRegex(ValueError,'绑定'):cn.save(self.udid,{'mode':'wifi'})
 def test_stopping_one_wireless_process_preserves_other(self):
  first=Mock();first.poll.return_value=0;second=Mock();cn._PROCESSES.update({self.udid:first,'d'*40:second});cn.stop_device(self.udid);self.assertIn('d'*40,cn._PROCESSES);second.poll.assert_not_called()
 def test_failure_records_stage_and_bounded_route_evidence(self):
  trace={'stage':'wda_endpoint','candidates':['http://192.168.31.113:18100']*5}
  route=Mock(returncode=0,stdout='   interface: en0\n     gateway: 192.168.31.1\nother: omitted\n')
  with patch.object(cn.subprocess,'run',return_value=route) as run:
   path=cn.record_connection_failure(self.udid,trace,RuntimeError('unreachable'))
   evidence=json.loads(path.read_text());self.assertEqual(evidence['stage'],'wda_endpoint');self.assertEqual(evidence['routes'][0]['interface'],'en0');run.assert_called_once()
   self.assertNotIn('other',evidence['routes'][0]);self.assertEqual(run.call_args.kwargs['timeout'],2)
 def test_diagnostic_write_failure_preserves_original_error(self):
  with patch.object(cn,'_ensure_wifi',side_effect=RuntimeError('original connection error')),patch.object(cn,'record_connection_failure',side_effect=OSError('disk full')):
   with self.assertRaisesRegex(RuntimeError,'original connection error'):cn.ensure_wifi(self.udid)
 def test_manual_stop_does_not_create_failure_report(self):
  from runner import Halt
  with patch.object(cn,'_ensure_wifi',side_effect=Halt('stop')),patch.object(cn,'record_connection_failure') as record:
   with self.assertRaises(Halt):cn.ensure_wifi(self.udid)
   record.assert_not_called()

 def test_missing_installed_component_reports_specific_stage(self):
  process=Mock();process.poll.return_value=1
  def launch(*args,**kwargs):
   kwargs['stdout'].write(b'ERROR: The requested application com.igconsole.test.xctrunner is not installed.\n');kwargs['stdout'].flush();return process
  with patch.object(dm,'coredevice',return_value=self.details('fd00::1')),patch.object(cn,'inspect',side_effect=OSError('not ready')),patch.object(cn.subprocess,'Popen',side_effect=launch),patch.object(cn.subprocess,'run',return_value=Mock(returncode=0,stdout='')),patch.object(dm,'child_env',return_value={}),patch.object(dm,'xcode_directory',return_value=Path('/Xcode')):
   with self.assertRaisesRegex(RuntimeError,'未安装本机签名记录'):cn.ensure_wifi(self.udid,setting_override={'mode':'wifi'})
  evidence=json.loads((self.runtime/'connection-diagnostic.json').read_text());self.assertEqual(evidence['stage'],'component_missing')

class WdaProtocol(unittest.TestCase):
 def test_read_only_verification_over_real_http_and_redirect_rejection(self):
  from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
  import threading
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    if self.path=='/status':value={'ready':True,'build':{'productBundleIdentifier':'com.example.wda'},'os':{'version':'18.1'}}
    elif self.path=='/wda/device/info':value={'uuid':'stable-phone','name':'Test phone'}
    else:
     self.send_response(302);self.send_header('Location','http://8.8.8.8:8100/status');self.end_headers();return
    raw=json.dumps({'value':value}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}'
  try:
   self.assertEqual(cn.checked(url,'stable-phone')['ios'],'18.1')
   with self.assertRaisesRegex(RuntimeError,'指纹'):cn.checked(url,'another-phone')
   with self.assertRaisesRegex(RuntimeError,'重定向'):cn.get(url,'/redirect')
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
