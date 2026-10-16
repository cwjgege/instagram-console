"""Local device onboarding, signed WDA provisioning, and bundled Appium runtime."""
import atexit
import base64
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import ssl
import subprocess
import tempfile
import tarfile
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
from app_paths import ASSETS,DATA,FROZEN,PORT,SERVER,VERSION,lock_device

SERVER_PROCESS=None
SERVER_PID=None

def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');tmp.replace(path)

def xcode_directory():
    choices=[]
    result=subprocess.run(['/usr/bin/xcode-select','-p'],capture_output=True,text=True)
    if result.returncode==0:choices.append(Path(result.stdout.strip()))
    choices.extend(p/'Contents/Developer' for p in Path('/Applications').glob('Xcode*.app'))
    for path in choices:
        if (path/'usr/bin/xcodebuild').exists() and (path/'usr/bin/devicectl').exists():return path
    raise RuntimeError('未找到完整 Xcode。请从 App Store 安装，打开一次并完成必要组件安装。')

def child_env(vendor=None):
    env=os.environ.copy()
    env['DEVELOPER_DIR']=str(xcode_directory())
    env['PATH']=(str(vendor/'bin')+':' if vendor else '')+'/usr/bin:/bin:/usr/sbin:/sbin'
    for key in ['PYTHONHOME','PYTHONPATH','__PYVENV_LAUNCHER__','DYLD_LIBRARY_PATH','DYLD_FRAMEWORK_PATH']:
        original=env.pop(key+'_ORIG',None);env.pop(key,None)
        if original:env[key]=original
    return env

def check_xcode():
    directory=xcode_directory()
    result=subprocess.run([str(directory/'usr/bin/xcodebuild'),'-checkFirstLaunchStatus'],
        env=child_env(),capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError('Xcode 尚未完成首次设置。请打开 Xcode，由你确认协议并完成必要组件安装，然后重新检测。')
    return directory

def prepare_vendor(notify=lambda message:None):
    if not FROZEN and os.environ.get('IG_CONSOLE_VENDOR'):
        source=Path(os.environ['IG_CONSOLE_VENDOR']).resolve()
        if not (source/'bin/node').exists() or not (source/'node_modules/appium/index.js').exists():raise RuntimeError('开发运行环境路径无效')
        return source
    target=DATA/'runtime'/VERSION
    def ready():return (target/'.ready').exists() and (target/'bin/node').exists() and (target/'node_modules/appium/index.js').exists()
    if ready():return target
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'runtime-install.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if ready():return target
        source=ASSETS/'vendor'
        archive=ASSETS/'vendor.tar.gz'
        if not source.exists() and not archive.exists():raise RuntimeError('安装包缺少内置运行环境，请重新安装完整应用。')
        notify('首次启动：正在准备内置运行环境…')
        stage=target.with_name(VERSION+'.preparing')
        if stage.exists():shutil.rmtree(stage)
        stage.parent.mkdir(parents=True,exist_ok=True)
        if archive.exists():
            stage.mkdir()
            with tarfile.open(archive,'r:gz') as bundle:
                for item in bundle.getmembers():
                    location=(stage/item.name).resolve()
                    if not location.is_relative_to(stage.resolve()):raise RuntimeError('内置运行环境路径无效')
                    if item.issym() or item.islnk():
                        link=(location.parent/item.linkname).resolve()
                        if not link.is_relative_to(stage.resolve()):raise RuntimeError('内置运行环境链接无效')
                bundle.extractall(stage)
        else:shutil.copytree(source,stage,symlinks=False)
        (stage/'.ready').write_text(VERSION)
        if target.exists():shutil.rmtree(target)
        stage.replace(target)
    return target

def stop_owned_server():
    global SERVER_PROCESS,SERVER_PID
    pid=SERVER_PROCESS.pid if SERVER_PROCESS and SERVER_PROCESS.poll() is None else SERVER_PID
    if pid:
        command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
        expected=str(DATA/'runtime'/VERSION/'bin/node')
        if command.startswith(expected+' ') and '--base-path /igconsole' in command:
            try:os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:pass
    SERVER_PID=None
atexit.register(stop_owned_server)

def ensure_server(notify=lambda message:None):
    global SERVER_PROCESS,SERVER_PID
    vendor=prepare_vendor(notify)
    env=child_env(vendor)
    env['APPIUM_HOME']=str(DATA/'appium-home')
    home=Path(env['APPIUM_HOME']);cache=home/'node_modules/.cache/appium'
    template=json.loads((vendor/'driver-template.json').read_text())
    driver=template['drivers']['xcuitest'];driver_path=str(vendor/'node_modules/appium-xcuitest-driver')
    driver['installPath']=driver['installSpec']=driver_path
    write_json(cache/'extensions.yaml',template)  # JSON is valid YAML.
    write_json(home/'package.json',{'private':True,'name':'igconsole-local-runtime','version':VERSION})
    try:
        with urlopen(SERVER+'/status',timeout=2) as response:
            if json.load(response).get('value',{}).get('ready') is not None:
                try:SERVER_PID=int(json.loads((DATA/'server.json').read_text())['pid'])
                except (OSError,ValueError,KeyError,TypeError):pass
                return vendor
    except Exception:pass
    notify('正在启动内置手机控制服务…')
    log=DATA/'appium.log'
    with log.open('ab') as output:
        SERVER_PROCESS=subprocess.Popen([str(vendor/'bin/node'),str(vendor/'node_modules/appium/index.js'),
            '--address','127.0.0.1','--port',str(PORT),'--base-path','/igconsole',
            '--log-level','info'],cwd=vendor,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    SERVER_PID=SERVER_PROCESS.pid
    write_json(DATA/'server.json',{'pid':SERVER_PID,'runtime_version':VERSION})
    for _ in range(60):
        if SERVER_PROCESS.poll() is not None:raise RuntimeError('控制服务启动失败，请在设备向导打开诊断日志。')
        try:
            with urlopen(SERVER+'/status',timeout=1) as response:
                if json.load(response).get('value',{}).get('ready') is not None:return vendor
        except Exception:time.sleep(.5)
    raise RuntimeError('控制服务启动超时，请检查 Xcode 或诊断日志。')

def coredevice(args):
    DATA.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA,prefix='device-check-') as folder:
        output=Path(folder)/'result.json'
        result=subprocess.run(['/usr/bin/xcrun','devicectl',*args,'--timeout','20','--json-output',str(output),'--quiet'],
            env=child_env(),capture_output=True,text=True,timeout=30)
        if result.returncode or not output.exists():
            raise RuntimeError('无法读取 iPhone。请解锁、检查 USB 连接和“信任此电脑”，然后重新检测。')
        return json.loads(output.read_text())['result']

def wired_iphones(result):
    devices=[]
    for d in result.get('devices',[]):
        connection=d.get('connectionProperties',{});hardware=d.get('hardwareProperties',{})
        props=d.get('deviceProperties',{})
        if connection.get('transportType')!='wired' or hardware.get('deviceType')!='iPhone':continue
        devices.append({'udid':hardware.get('udid',''),'identifier':d['identifier'],
            'name':props.get('name','iPhone'),'model':hardware.get('marketingName','iPhone'),
            'ios':props.get('osVersionNumber','未知'),'paired':connection.get('pairingState')=='paired',
            'developer':props.get('developerModeStatus')=='enabled'})
    return devices

def usb_serials():
    tree=plistlib.loads(subprocess.check_output(['/usr/sbin/ioreg','-p','IOUSB','-l','-a'],timeout=8));found=set()
    def walk(n):
        if isinstance(n,dict):
            if n.get('USB Product Name')=='iPhone':found.add(str(n.get('USB Serial Number','')).replace('-','').lower())
            for c in n.get('IORegistryEntryChildren',[]):walk(c)
        elif isinstance(n,list):
            for c in n:walk(c)
    walk(tree);return found

def scan_devices():
    physical=usb_serials()
    try:modern=wired_iphones(coredevice(['list','devices']))
    except RuntimeError:modern=[]
    modern=[d for d in modern if d['udid'].replace('-','').lower() in physical]
    try:
        r=subprocess.run(['/usr/bin/xcrun','xcdevice','list','--timeout','5'],env=child_env(),capture_output=True,text=True,timeout=15)
        rows=json.loads(r.stdout) if r.returncode==0 else []
    except (ValueError,subprocess.TimeoutExpired):rows=[]
    known={d['udid'].lower() for d in modern}
    old=[d for d in rows if not d.get('simulator') and str(d.get('identifier','')).replace('-','').lower() in physical and d.get('identifier','').lower() not in known]
    represented={d.get('udid',d['identifier']).replace('-','').lower() for d in modern+old}
    old.extend({'identifier':serial,'name':'iPhone','modelName':'iPhone'} for serial in physical-represented)
    if old:
        from runtime_manager import probe_legacy
        try:values={d['udid'].lower():d for d in probe_legacy([d['identifier'] for d in old])}
        except (RuntimeError,subprocess.TimeoutExpired):values={}
        for d in old:
            info=values.get(d['identifier'].lower(),{})
            modern.append({'udid':d['identifier'],'identifier':d['identifier'],'name':info.get('name',d.get('name','iPhone')),
                'model':d.get('modelName',info.get('product','iPhone')),'ios':info.get('ios',d.get('operatingSystemVersion','未知').split()[0]),
                'paired':info.get('paired',False),'developer':info.get('developer',False)})
    return modern

def certificate_info(der):
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder)/'certificate.pem';path.write_text(ssl.DER_cert_to_PEM_cert(der))
        return ssl._ssl._test_decode_cert(str(path))

def signing_teams():
    identities=subprocess.run(['/usr/bin/security','find-identity','-v','-p','codesigning'],capture_output=True,text=True).stdout
    allowed=set(re.findall(r'\b([A-Fa-f0-9]{40})\b',identities))
    pem=subprocess.run(['/usr/bin/security','find-certificate','-a','-p'],capture_output=True,text=True).stdout
    teams={}
    for block in re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',pem,re.S):
        try:
            der=ssl.PEM_cert_to_DER_cert(block)
            if hashlib.sha1(der).hexdigest().upper() not in allowed:continue
            info=certificate_info(der)
            subject={k:v for group in info['subject'] for k,v in group}
            if not subject.get('commonName','').startswith(('Apple Development:','iPhone Developer:')):continue
            team=subject.get('organizationalUnitName','')
            if re.fullmatch('[A-Z0-9]{10}',team):teams[team]=subject.get('commonName',team)
        except (ValueError,ssl.SSLError):continue
    return teams

def device_dir(udid):
    if not re.fullmatch('[A-Fa-f0-9-]{10,50}',udid):raise ValueError('设备标识无效，请重新检测')
    return DATA/'devices'/udid

def make_payload(device,team):
    if not re.fullmatch('[A-Z0-9]{10}',team):raise ValueError('Team ID 必须为 10 位大写字母或数字')
    runtime=device_dir(device['udid']);runtime.mkdir(parents=True,exist_ok=True)
    from runtime_manager import endpoints
    ports=endpoints(device['udid'])
    return {'capabilities':{'alwaysMatch':{
        'platformName':'iOS','appium:automationName':'XCUITest','appium:udid':device['udid'],
        'appium:deviceName':device['name'],'appium:noReset':True,'appium:shouldTerminateApp':False,
        'appium:xcodeOrgId':team,'appium:xcodeSigningId':'Apple Development',
        'appium:updatedWDABundleId':'com.igconsole.wda.'+team.lower(),
        'appium:allowProvisioningDeviceRegistration':True,'appium:showXcodeLog':True,
        'appium:useNewWDA':False,'appium:usePrebuiltWDA':True,'appium:derivedDataPath':str(runtime/'DerivedData'),
        'appium:wdaLocalPort':ports['wda'],'appium:mjpegServerPort':ports['mjpeg'],'appium:wdaRemotePort':8100,
        'appium:wdaLaunchTimeout':120000,'appium:wdaConnectionTimeout':20000,
        'appium:waitForIdleTimeout':1,'appium:newCommandTimeout':300},'firstMatch':[{}]}}

def profile_record(app,udid,team):
    raw=subprocess.check_output(['/usr/bin/security','cms','-D','-i',str(app/'embedded.mobileprovision')],stderr=subprocess.PIPE)
    profile=plistlib.loads(raw)
    if team not in profile.get('TeamIdentifier',[]) or udid not in profile.get('ProvisionedDevices',[]):
        raise RuntimeError('新签名的团队或设备不匹配，未保存为可用配置。')
    info=plistlib.loads((app/'Info.plist').read_bytes())
    expiration=profile['ExpirationDate'].replace(tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as folder:
        prefix=str(Path(folder)/'cert')
        subprocess.run(['/usr/bin/codesign','-d','--extract-certificates='+prefix,str(app)],check=True,capture_output=True)
        leaf=certificate_info(Path(prefix+'0').read_bytes())
        cert_exp=datetime.fromtimestamp(ssl.cert_time_to_seconds(leaf['notAfter']),timezone.utc)
    return {'udid':udid,'team_id':team,'bundle_id':info['CFBundleIdentifier'],
        'profile_name':profile.get('Name',''),'profile_expires_at':expiration.isoformat(),
        'certificate_expires_at':cert_exp.isoformat(),'expires_at':min(expiration,cert_exp).isoformat(),
        'installed_at':datetime.now(timezone.utc).isoformat(),'profile_sha256':hashlib.sha256(raw).hexdigest()}

def expiry_state(record,hours=48,now=None):
    if not record:return 'unknown',None
    try:
        expires=datetime.fromisoformat(record['expires_at'])
        if expires.tzinfo is None:expires=expires.replace(tzinfo=timezone.utc)
        remaining=(expires-(now or datetime.now(timezone.utc))).total_seconds()/3600
    except (KeyError,TypeError,ValueError):return 'unknown',None
    return ('expired' if remaining<=0 else 'soon' if remaining<=hours else 'valid'),remaining

def provision(device,team,notify=lambda message:None):
    """Build/sign/install WDA for the selected USB phone. Never opens Instagram."""
    with lock_device(device['udid']):return _provision(device,team,notify)

def _provision(device,team,notify):
    runtime=device_dir(device['udid']);runtime.mkdir(parents=True,exist_ok=True)
    with (runtime/'runner.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('请先停止这台手机的浏览任务，再重新签名。')
        fresh=next((d for d in scan_devices() if d['udid']==device['udid']),None)
        if not fresh:raise RuntimeError('所选 iPhone 未通过 USB 连接，请重新检测。')
        if not fresh['paired']:raise RuntimeError('请先在手机上信任此电脑，再重新检测。')
        if not fresh['developer']:raise RuntimeError('请开启手机开发者模式，重启并确认开启后再安装。')
        check_xcode()
        previous=signing_record(device['udid'])
        from runtime_manager import ensure_server as ensure_device_server,build_legacy,ensure_legacy
        from runner import call as transport
        endpoint=ensure_device_server(device['udid'],notify)
        def call(method,path,data=None,timeout=35):return transport(method,path,data,timeout,server=endpoint['url'])
        old=runtime/'active-session.json'
        if old.exists():
            try:call('DELETE','/session/'+json.loads(old.read_text())['sessionId'],timeout=10)
            except Exception:pass
            old.unlink(missing_ok=True)
        payload=make_payload(fresh,team)
        if previous and previous.get('team_id')==team:
            payload['capabilities']['alwaysMatch']['appium:updatedWDABundleId']=previous['bundle_id'].removesuffix('.xctrunner')
        legacy=str(fresh.get('ios','')).split('.')[0]=='16'
        if legacy:
            bundle=payload['capabilities']['alwaysMatch']['appium:updatedWDABundleId']
            app=build_legacy(fresh,team,bundle,notify)
            record=profile_record(app,device['udid'],team);record['controller']='legacy16'
            url=ensure_legacy(device['udid'],record,notify,install=app)
            payload['capabilities']['alwaysMatch']['appium:webDriverAgentUrl']=url
        build=json.loads(json.dumps(payload));caps=build['capabilities']['alwaysMatch']
        caps['appium:usePrebuiltWDA']=False;caps['appium:useNewWDA']=False
        notify('正在编译、签名并验证控制组件，不主动卸载旧控件。请保持手机解锁；若提示开发者未受信任，请在手机完成信任后重试。')
        try:value=call('POST','/session',build,timeout=360)
        except Exception as exc:
            raise RuntimeError('控制组件未完成安装或启动，不能开始浏览。详细 Xcode 错误已记录在 appium.log；请通过当前聊天发送日志以定位原因。') from exc
        value['server_url']=endpoint['url'];write_json(old,value)
        if not legacy:
            app=runtime/'DerivedData/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app'
            record=profile_record(app,device['udid'],team);record['controller']='modern'
        if previous:record['previous_expires_at']=previous.get('expires_at')
        state,_=expiry_state(record)
        if state=='expired':raise RuntimeError('签名已到期，请检查 Xcode 证书后重试。')
        write_json(runtime/'session-request.json',payload)
        write_json(runtime/'signing.json',record)
        notify('控制组件已安装并连接，签名有效期已读取。')
        return record

def signing_record(udid):
    if not udid:return None
    path=device_dir(udid)/'signing.json'
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return None

def close_other_sessions(keep=None):
    """Compatibility no-op: each device owns its sessions; never close other phones."""
    return 0
