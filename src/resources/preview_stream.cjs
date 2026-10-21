/* Read-only USB MJPEG client. No Appium session creation or UI commands. */
const fs=require('fs'),path=require('path');
const {createRequire}=require('module');
const cfg=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
if(!/^[a-fA-F0-9-]{10,50}$/.test(cfg.udid)||!Number.isInteger(cfg.port)||cfg.port<1025||cfg.port>65535)process.exit(2);
const req=createRequire(path.join(cfg.vendor,'node_modules/appium-xcuitest-driver/package.json'));
let socket;
process.on('SIGTERM',()=>{if(socket)socket.destroy();process.exit(0)});
process.on('SIGINT',()=>{if(socket)socket.destroy();process.exit(0)});
(async()=>{
 if(cfg.host){
  const transport=require(cfg.tls?'tls':'net');
  socket=cfg.tls?transport.connect({host:cfg.host,port:cfg.port,servername:cfg.host}):transport.createConnection({host:cfg.host,port:cfg.port});
  await new Promise((resolve,reject)=>{socket.once(cfg.tls?'secureConnect':'connect',resolve);socket.once('error',reject)});
 }else{const ios=req('appium-ios-device');socket=await ios.utilities.connectPort(cfg.udid,cfg.port)}
 socket.on('error',()=>process.exit(1));socket.on('end',()=>process.exit(1));
 socket.pipe(process.stdout);socket.write('GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n');
})().catch(()=>process.exit(1));
