import json,shutil,subprocess,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
NODE=shutil.which('node');UDID='00008030-0011223344556677'
@unittest.skipUnless(NODE,'Node.js is required for the paired identity transport fixture')
class PairedTransport(unittest.TestCase):
 def run_probe(self,actual):
  routes=[]
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    routes.append(self.path)
    value={'ready':True,'build':{'productBundleIdentifier':'com.igconsole.wda.example'},'os':{'version':'27.0.1'}} if self.path=='/status' else {'uuid':'paired-phone-fingerprint'}
    raw=json.dumps({'value':value}).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   with tempfile.TemporaryDirectory() as folder:
    vendor=Path(folder);driver=vendor/'node_modules/appium-xcuitest-driver';module=driver/'node_modules/appium-ios-device';module.mkdir(parents=True);(driver/'package.json').write_text('{}')
    code="const net=require('net');exports.utilities={startLockdownSession:async()=>({getValue:async()=>ACTUAL,close(){}}),connectPort:async(id,port)=>{if(id!==TARGET||port!==8100)throw Error('Wrong route');return new Promise((resolve,reject)=>{const socket=net.createConnection({host:'127.0.0.1',port:PORT},()=>resolve(socket));socket.once('error',reject)})}};"
    code=code.replace('ACTUAL',json.dumps(actual)).replace('TARGET',json.dumps(UDID)).replace('PORT',str(server.server_address[1]));(module/'index.js').write_text(code)
    request=vendor/'request.json';request.write_text(json.dumps({'vendor':str(vendor),'mode':'identity','udid':UDID,'port':8100}))
    result=subprocess.run([NODE,'src/resources/ios_device.cjs',str(request)],capture_output=True,text=True,timeout=10)
    return result,json.loads(result.stdout),routes
  finally:server.shutdown();server.server_close();thread.join(2)
 def test_reads_status_and_fingerprint_over_selected_device_port(self):
  result,value,routes=self.run_probe(UDID)
  self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(value['udid'],UDID);self.assertEqual(value['fingerprint'],'paired-phone-fingerprint');self.assertEqual(routes,['/status','/wda/device/info'])
 def test_wrong_lockdown_identity_prevents_any_http_reads(self):
  result,value,routes=self.run_probe('00008030-008899AABBCCDDEE')
  self.assertNotEqual(result.returncode,0);self.assertIn('mismatch',value['error']);self.assertEqual(routes,[])
