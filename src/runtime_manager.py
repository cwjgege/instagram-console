"""One Appium process and one optional legacy bridge per physical phone."""
import atexit,fcntl,json,os,re,signal,socket,subprocess,threading,time,hashlib,shutil,plistlib,tempfile
from pathlib import Path
from urllib.request import urlopen
from app_paths import ASSETS,FROZEN,VERSION
_GUARD=threading.RLock();_LOCKS={};_PROCESSES={}

def device_lock(udid):
    with _GUARD:return _LOCKS.setdefault(udid,threading.RLock())

def endpoints(udid):
    import device_manager as dm
    dm.device_dir(udid)  # Validate before using any path.
    root=dm.DATA;root.mkdir(parents=True,exist_ok=True)
    with (root/'ports.lock').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        file=root/'ports.json';rows=json.loads(file.read_text()) if file.exists() else {}
        assigned=[]
        if not isinstance(rows,dict):raise RuntimeError('设备端口记录无效，请检查 ports.json。')
        for item in rows.values():
            if not isinstance(item,dict) or set(item)!=set(('appium','wda','mjpeg')) or any(type(p) is not int or not 1024<p<65536 for p in item.values()):raise RuntimeError('设备端口记录无效，请检查 ports.json。')
            assigned.extend(item.values())
        if len(assigned)!=len(set(assigned)):raise RuntimeError('设备端口记录重复，请检查 ports.json。')
        key=udid.lower()
        if key not in rows:
            used={v for ports in rows.values() for v in ports.values()}
            for i in range(200):
                ports={'appium':19000+i*4,'wda':19001+i*4,'mjpeg':19002+i*4}
                sockets=[]
                try:
                    if any(p in used for p in ports.values()):continue
                    for p in ports.values():
                        s=socket.socket();sockets.append(s);s.bind(('127.0.0.1',p))
                    rows[key]=ports;dm.write_json(file,rows);break
                except OSError:continue
                finally:
                    for s in sockets:s.close()
            else:raise RuntimeError('没有可用的设备控制端口，请关闭闲置控制服务。')
        ports=rows[key]
        if set(ports)!=set(('appium','wda','mjpeg')) or any(type(p) is not int or not 1024<p<65536 for p in ports.values()):raise RuntimeError('设备端口记录无效，请检查 ports.json。')
    return {**ports,'url':f"http://127.0.0.1:{ports['appium']}/igconsole"}

def vendor():
    import device_manager as dm
    return dm.prepare_vendor()

def probe_legacy(targets):
    import device_manager as dm
    v=vendor()
    with tempfile.TemporaryDirectory(dir=dm.DATA,prefix='usb-probe-') as folder:
        request=Path(folder)/'request.json';dm.write_json(request,{'vendor':str(v),'mode':'probe','targets':targets})
        r=subprocess.run([str(v/'bin/node'),str(ASSETS/'resources/ios_device.cjs'),str(request)],env=dm.child_env(v),capture_output=True,text=True,timeout=25)
        if r.returncode:raise RuntimeError('无法读取旧版 iPhone 的信任状态，请解锁并重新连接。')
        for line in reversed(r.stdout.splitlines()):
            try:
                value=json.loads(line)
                if isinstance(value,list):return value
            except ValueError:pass
        raise RuntimeError('旧版设备检测没有返回结果。')

def _ready(url):
    try:
        with urlopen(url+'/status',timeout=1) as r:return json.load(r).get('value',{}).get('ready') is True
    except Exception:return False

def _check(stop):
    if stop is not None and stop.is_set():
        from runner import Halt
        raise Halt('已手动停止')

def _owned(pid,v,port,kind):
    command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
    if not command.startswith(str(v/'bin/node')+' '):return False
    return (f'--port {port} ' in command and '--base-path /igconsole' in command) if kind=='appium' else ('ios_device.cjs' in command)

class ExistingProcess:
    def __init__(self,pid):self.pid=pid
    def poll(self):
        try:os.kill(self.pid,0);return None
        except ProcessLookupError:return 0

def _stop(key):
    entry=_PROCESSES.pop(key,None)
    if entry:
        process,v,port=entry
        if process.poll() is None and _owned(process.pid,v,port,key[1]):
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass

def stop_device(udid):
    import connections
    connections.stop_device(udid)
    with device_lock(udid):
        _stop((udid,'appium'));_stop((udid,'legacy'))

def stop_all():
    import connections
    connections.stop_all()
    for udid in {k[0] for k in list(_PROCESSES)}:stop_device(udid)
atexit.register(stop_all)

def ensure_server(udid,notify=lambda s:None,stop=None):
    import device_manager as dm
    with device_lock(udid):
        e=endpoints(udid);v=vendor();runtime=dm.device_dir(udid);runtime.mkdir(parents=True,exist_ok=True)
        info=runtime/'server.json';key=(udid,'appium')
        if _ready(e['url']):
            try:pid=json.loads(info.read_text())['pid']
            except (OSError,ValueError,KeyError):raise RuntimeError('设备端口被其他服务占用，未接管该连接。')
            if not _owned(pid,v,e['appium'],'appium'):raise RuntimeError('设备端口被其他服务占用，未接管该连接。')
            if key not in _PROCESSES or _PROCESSES[key][0].poll() is not None:_PROCESSES[key]=(ExistingProcess(pid),v,e['appium'])
            return e
        _stop(key);env=dm.child_env(v);home=runtime/'appium-home';env['APPIUM_HOME']=str(home)
        template=json.loads((v/'driver-template.json').read_text());driver=template['drivers']['xcuitest'];driver['installPath']=driver['installSpec']=str(v/'node_modules/appium-xcuitest-driver')
        dm.write_json(home/'node_modules/.cache/appium/extensions.yaml',template);dm.write_json(home/'package.json',{'private':True,'name':'igconsole-device-runtime','version':VERSION})
        notify('正在启动此设备的独立控制服务…')
        with (runtime/'appium.log').open('ab') as log:
            p=subprocess.Popen([str(v/'bin/node'),str(v/'node_modules/appium/index.js'),'--address','127.0.0.1','--port',str(e['appium']),'--base-path','/igconsole','--log-level','info'],cwd=v,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        _PROCESSES[key]=(p,v,e['appium']);dm.write_json(info,{'pid':p.pid,'runtime_version':VERSION})
        try:
            for _ in range(90):
                _check(stop)
                if p.poll() is not None:raise RuntimeError('此设备的控制服务启动失败，请查看该设备 appium.log。')
                if _ready(e['url']):return e
                time.sleep(.3)
            raise RuntimeError('此设备的控制服务启动超时。')
        except BaseException:_stop(key);raise

def ensure_legacy(udid,record,notify=lambda s:None,stop=None,install=None):
    import device_manager as dm
    with device_lock(udid):
        e=endpoints(udid);v=vendor();runtime=dm.device_dir(udid);url=f"http://127.0.0.1:{e['wda']}";key=(udid,'legacy')
        if not install and key in _PROCESSES and _PROCESSES[key][0].poll() is None and _ready(url):return url
        _stop(key);cfg={'vendor':str(v),'udid':udid,'bundle':record['bundle_id'],'wda_port':e['wda'],'mjpeg_port':e['mjpeg']}
        if install:cfg['install']=str(install)
        request=runtime/'legacy-request.json';dm.write_json(request,cfg)
        notify('正在启动 iOS 16 兼容控制组件…')
        with (runtime/'legacy.log').open('ab') as log:
            p=subprocess.Popen([str(v/'bin/node'),str(ASSETS/'resources/ios_device.cjs'),str(request)],cwd=v,env=dm.child_env(v),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        _PROCESSES[key]=(p,v,e['wda'])
        try:
            deadline=time.monotonic()+(180 if install else 90)
            while time.monotonic()<deadline:
                _check(stop)
                if p.poll() is not None:raise RuntimeError('iOS 16 控制组件未能启动，请查看该设备 legacy.log；检查解锁、信任和开发者模式。')
                if _ready(url):return url
                time.sleep(.5)
            raise RuntimeError('iOS 16 控制组件启动超时，请检查手机解锁及开发者信任，详情见 legacy.log。')
        except BaseException:_stop(key);raise

def build_legacy(device,team,bundle,notify):
    import device_manager as dm
    runtime=dm.device_dir(device['udid']);v=vendor();project=runtime/'legacy-wda';source=v/'legacy-wda'
    if not source.exists():raise RuntimeError('安装包缺少 iOS 16 兼容组件，请重新安装完整版本。')
    if not project.exists():shutil.copytree(source,project,symlinks=True)
    derived=runtime/'LegacyDerivedData';env=dm.child_env(v);xcode=dm.xcode_directory()
    args=[str(xcode/'usr/bin/xcodebuild'),'build-for-testing','-allowProvisioningUpdates','-allowProvisioningDeviceRegistration','-project',str(project/'WebDriverAgent.xcodeproj'),'-scheme','WebDriverAgentRunner','-derivedDataPath',str(derived),'-destination','id='+device['udid'],'IPHONEOS_DEPLOYMENT_TARGET=16.0','DEVELOPMENT_TEAM='+team,'CODE_SIGN_IDENTITY=Apple Development','PRODUCT_BUNDLE_IDENTIFIER='+bundle,'GCC_TREAT_WARNINGS_AS_ERRORS=0','COMPILER_INDEX_STORE_ENABLE=NO']
    notify('正在为这台 iOS 16 手机编译并签名兼容组件…')
    with (runtime/'device-setup.log').open('ab') as log:
        result=subprocess.run(args,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=360)
    if result.returncode:raise RuntimeError('iOS 16 控制组件编译失败，详情见此设备 device-setup.log。')
    app=derived/'Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app';exe=app/'WebDriverAgentRunner-Runner'
    with tempfile.TemporaryDirectory(dir=runtime) as folder:
        prefix=str(Path(folder)/'cert');subprocess.run(['codesign','-d','--extract-certificates='+prefix,str(app)],check=True,capture_output=True)
        identity=hashlib.sha1(Path(prefix+'0').read_bytes()).hexdigest().upper()
    subprocess.run(['/usr/bin/xcrun','--sdk','iphoneos','clang','-target','arm64-apple-ios16.0','-fobjc-arc','-framework','UIKit','-framework','Foundation','-Wl,-rpath,/Developer/usr/lib,-rpath,/Developer/Library/Frameworks,-rpath,/Developer/Library/PrivateFrameworks',str(ASSETS/'resources/legacy_main.m'),'-o',str(exe)],env=env,check=True,capture_output=True,timeout=60)
    info=app/'Info.plist';values=plistlib.loads(info.read_bytes());values.pop('UIApplicationSceneManifest',None);info.write_bytes(plistlib.dumps(values))
    # iOS16 supplies the matching XCTest frameworks on its developer disk image.
    if (app/'Frameworks').exists():shutil.rmtree(app/'Frameworks')
    (app/'Frameworks').mkdir()
    ent=derived/'Build/Intermediates.noindex/WebDriverAgent.build/Debug-iphoneos/WebDriverAgentRunner.build/WebDriverAgentRunner.xctest.xcent'
    subprocess.run(['codesign','--force','--sign',identity,'--timestamp=none','--entitlements',str(ent),str(app)],env=env,check=True,capture_output=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
    return app
