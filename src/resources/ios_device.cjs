/* Local iOS USB discovery and preinstalled iOS16 XCTest launch. No social actions. */
const fs=require('fs'),path=require('path'),net=require('net');
const {createRequire}=require('module');const {pathToFileURL}=require('url');
const cfg=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const req=createRequire(path.join(cfg.vendor,'node_modules/appium-xcuitest-driver/package.json'));
const ios=req('appium-ios-device');let test,servers=[];
const write=(value)=>process.stdout.write(JSON.stringify(value)+'\n');
async function probe(){
 const ids=await ios.utilities.getConnectedDevices();const result=[];
 for(const udid of cfg.targets||ids){if(!ids.includes(udid))continue;
  try{let info=await ios.utilities.getDeviceInfo(udid);let paired=false,developer=false,l;
   try{l=await ios.utilities.startLockdownSession(udid);paired=true;developer=!!await l.getValue({Key:'DeveloperModeStatus',Domain:'com.apple.security.mac.amfi'});}catch(e){}finally{if(l)l.close()}
   result.push({udid,name:info.DeviceName,ios:info.ProductVersion,product:info.ProductType,paired,developer});
  }catch(e){result.push({udid,paired:false,developer:false,error:String(e.message).slice(0,180)})}
 }write(result);
}
async function forward(local,remote){
 const server=net.createServer(async socket=>{let device;socket.on('error',()=>{if(device)device.destroy()});
  try{device=await ios.utilities.connectPort(cfg.udid,remote);device.on('error',()=>socket.destroy());socket.pipe(device);device.pipe(socket);socket.on('close',()=>device.destroy());}catch(e){socket.destroy()}
 });servers.push(server);await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(local,'127.0.0.1',resolve)});
}
async function launch(){
 if(!/^[a-fA-F0-9-]{10,50}$/.test(cfg.udid))throw Error('Invalid device identifier');
 if(cfg.install){const {RealDevice}=await import(pathToFileURL(path.join(cfg.vendor,'node_modules/appium-xcuitest-driver/build/lib/device/real-device-management.js')).href);
  const d=new RealDevice(cfg.udid,{});d.isAppInstalled=async()=>{let p=await ios.services.startInstallationProxyService(cfg.udid);try{return !!(await p.lookupApplications({bundleIds:[cfg.bundle]}))[cfg.bundle]}finally{p.close()}};
  await d.install(cfg.install,cfg.bundle,{timeoutMs:120000});write({phase:'installed'});
 }
 test=new ios.Xctest(cfg.udid,cfg.bundle,null,{env:{USE_PORT:'8100',MJPEG_SERVER_PORT:String(cfg.mjpeg_port),WDA_PRODUCT_BUNDLE_IDENTIFIER:cfg.bundle,DYLD_FRAMEWORK_PATH:'/Developer/Library/Frameworks:/Developer/Library/PrivateFrameworks',DYLD_LIBRARY_PATH:'/Developer/usr/lib'}});
 // Finish configuration close before closing AFC; otherwise its close callback times out.
 test._writeConfigurationToDevice=async function(configuration,file){const house=await ios.services.startHouseArrestService(cfg.udid);let afc;
  try{afc=await house.vendContainer(cfg.bundle);let stream=await afc.createWriteStream(file,{});await new Promise((resolve,reject)=>{stream.once('error',reject);stream.end(configuration.getBytes(),resolve)})}finally{if(afc)afc.close();house.close()}
 };
 await test.start();await forward(cfg.wda_port,8100);write({phase:'forward_ready'});
}
function stop(){for(const s of servers)s.close();if(test)test.stop();process.exit(0)}
process.on('SIGTERM',stop);process.on('SIGINT',stop);
(cfg.mode==='probe'?probe():launch()).catch(e=>{write({error:String(e.message).slice(0,1000)});if(test)test.stop();for(const s of servers)s.close();process.exit(1)});
