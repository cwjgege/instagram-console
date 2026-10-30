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
from setup_errors import SetupError,classify_setup_error,log_positions,attempt_text

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
    env['PATH']=(str(vendor/'bin')+':' if vendor else '')+str(xcode_directory()/'usr/bin')+':/usr/bin:/bin:/usr/sbin:/sbin'
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

def developer_mode(value):
    if value is True or value=='enabled':return True
    if value is False or value=='disabled':return False
    return None

def resolve_developer_modes(devices):
    pending=[d for d in devices if d.get('paired') and d.get('developer') is None]
    if not pending:return devices
    from runtime_manager import probe_legacy
    try:values=probe_legacy([d['udid'] for d in pending])
    except (RuntimeError,subprocess.SubprocessError,OSError):return devices
    normalize=lambda value:str(value).replace('-','').lower()
    by_id={normalize(d.get('udid','')):d for d in values}
    for device in pending:
        info=by_id.get(normalize(device['udid']),{})
        if info.get('paired') is True and isinstance(info.get('developer'),bool):
            device['developer']=info['developer'];device['developer_source']='lockdown'
    return devices

def wired_iphones(result):
    devices=[]
    for d in result.get('devices',[]):
        connection=d.get('connectionProperties',{});hardware=d.get('hardwareProperties',{})
        props=d.get('deviceProperties',{})
        if connection.get('transportType')!='wired' or hardware.get('deviceType')!='iPhone':continue
        devices.append({'udid':hardware.get('udid',''),'identifier':d['identifier'],
            'name':props.get('name','iPhone'),'model':hardware.get('marketingName','iPhone'),
            'ios':props.get('osVersionNumber','未知'),'paired':connection.get('pairingState')=='paired',
            'developer':developer_mode(props.get('developerModeStatus'))})
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
    if not physical:return []
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
                'paired':info.get('paired',False),'developer':info.get('developer')})
    return resolve_developer_modes(modern)

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

def cached_team_profiles(team, home=None):
    """Read local Xcode profiles only; these do not establish live Apple registrations."""
    if not re.fullmatch('[A-Z0-9]{10}',team):raise ValueError('请选择有效的签名团队。')
    home=Path.home() if home is None else Path(home)
    folders=[home/'Library/Developer/Xcode/UserData/Provisioning Profiles',home/'Library/MobileDevice/Provisioning Profiles']
    profiles=[]
    for folder in folders:
        for path in folder.glob('*.mobileprovision'):
            try:
                result=subprocess.run(['/usr/bin/security','cms','-D','-i',str(path)],capture_output=True,timeout=10,check=True)
                data=plistlib.loads(result.stdout)
                if team not in data.get('TeamIdentifier',[]):continue
                devices=data.get('ProvisionedDevices',[])
                if not isinstance(devices,list) or not devices:continue
                expiration=data.get('ExpirationDate')
                if not isinstance(expiration,datetime):continue
                if expiration.tzinfo is None:expiration=expiration.replace(tzinfo=timezone.utc)
                profiles.append({'expires_at':expiration.isoformat(),'devices':[d for d in devices if isinstance(d,str)]})
            except (OSError,ValueError,TypeError,subprocess.SubprocessError,plistlib.InvalidFileException):continue
    return sorted(profiles,key=lambda p:p['expires_at'],reverse=True)

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

def compatibility_error(device):
    try:major=int(str(device.get('ios','')).split('.')[0])
    except ValueError:return '尚未读取到手机系统版本，请解锁后重新检测 USB 连接。'
    return '当前版本支持 iOS 16 及以上；这台手机的系统版本暂不支持。' if major<16 else None


def validate_device(device):
    if compatibility_error(device):raise SetupError('unsupported_ios',compatibility_error(device),'设备支持')
    if not device.get('paired'):raise SetupError('pairing','手机尚未信任此电脑。请在 Finder 中选择手机并完成双方的信任提示；这不同于 VPN 与设备管理里的开发者 App 信任。','设备准备')
    if device.get('developer') is None:raise SetupError('developer_mode_unconfirmed','尚未确认手机开发者模式状态；请保持手机解锁并重新检测，或在 Xcode 设备窗口检查连接准备状态。未读到状态不代表未开启。','设备准备')
    if not device.get('developer'):raise SetupError('developer_mode','请开启手机开发者模式，重启并确认开启后再安装。','设备准备')


def controller_apps(udid,bundles=None):
    device_dir(udid);vendor=prepare_vendor();DATA.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA,prefix='controller-probe-') as folder:
        file=Path(folder)/'request.json';write_json(file,{'vendor':str(vendor),'mode':'apps','udid':udid,'bundles':bundles})
        value=subprocess.run([str(vendor/'bin/node'),str(ASSETS/'resources/ios_device.cjs'),str(file)],env=child_env(vendor),capture_output=True,text=True,timeout=15)
        if value.returncode:raise RuntimeError('无法读取手机上的控制组件，请保持 USB 连接并解锁。')
        for line in reversed(value.stdout.splitlines()):
            try:
                result=json.loads(line)
                if isinstance(result,list):return result
            except ValueError:pass
        raise RuntimeError('手机没有返回控制组件安装信息。')


def controller_identity(udid,port=8100):
    """Bind WDA identity to a trusted USB/Network usbmux device, never to a LAN IP."""
    if not re.fullmatch(r'(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{16}|[a-fA-F0-9]{40})',udid):raise ValueError('需要真实设备编号才能核验指纹。')
    if type(port) is not int or not 1<=port<=65535:raise ValueError('控制组件端口无效。')
    vendor=prepare_vendor();DATA.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA,prefix='identity-probe-') as folder:
        request=Path(folder)/'request.json';write_json(request,{'vendor':str(vendor),'mode':'identity','udid':udid,'port':port})
        result=subprocess.run([str(vendor/'bin/node'),str(ASSETS/'resources/ios_device.cjs'),str(request)],env=child_env(vendor),capture_output=True,text=True,timeout=20)
        try:value=json.loads(result.stdout.splitlines()[-1])
        except (ValueError,IndexError):raise RuntimeError('未能通过已配对设备核验控件指纹，保留原绑定。') from None
        if result.returncode or not isinstance(value,dict) or value.get('error'):
            detail=str(value.get('error','核验进程异常'))[:300] if isinstance(value,dict) else '核验结果格式异常'
            raise RuntimeError('未能通过已配对设备核验控件指纹，保留原绑定：'+detail)
        if str(value.get('udid','')).replace('-','').lower()!=udid.replace('-','').lower() or value.get('port')!=port:raise RuntimeError('指纹核验返回了其他设备或端口，保留原绑定。')
        return value


def setup_status(udid):
    if not udid:return None
    try:
        value=json.loads((device_dir(udid)/'setup-state.json').read_text())
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


def setup_blocker(udid):
    state=setup_status(udid)
    return state.get('message') if state and state.get('status')!='ready' else None


def stage_installed_candidate(device,team):
    """A signed build becomes resumable only when that bundle exists on this phone."""
    runtime=device_dir(device['udid']);legacy=str(device.get('ios','')).split('.')[0]=='16'
    derived=runtime/('LegacyDerivedData' if legacy else 'DerivedData')
    try:
        record=profile_record(derived/'Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app',device['udid'],team)
        record['controller']='legacy16' if legacy else 'modern'
        if expiry_state(record)[0] in ('unknown','expired'):return False
        if not controller_apps(device['udid'],[record['bundle_id']]):return False
        write_json(runtime/'pending-setup.json',{'team_id':team,'record':record});return True
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):return False


def setup_attempt(device,team,job):
    runtime=device_dir(device['udid']);runtime.mkdir(parents=True,exist_ok=True);positions=log_positions(runtime)
    try:return job()
    except Exception as exc:
        issue=classify_setup_error(exc,attempt_text(runtime,positions))
        state={'status':'failed','code':issue.code,'stage':issue.stage,'message':str(issue),'updated_at':datetime.now(timezone.utc).isoformat()}
        if issue.code in ('network_validation','developer_untrusted','signature_or_trust','locked','automation_timeout','automation_startup','connection','unknown'):
            if stage_installed_candidate(device,team):state['status']='awaiting_trust' if issue.code=='developer_untrusted' else 'awaiting_validation'
        try:pending=json.loads((runtime/'pending-setup.json').read_text());state['can_verify']=pending.get('team_id')==team
        except (OSError,ValueError,AttributeError):state['can_verify']=False
        write_json(runtime/'setup-state.json',state)
        if issue is exc:raise
        raise issue from exc


def provision(device,team,notify=lambda message:None):
    """Build/sign/install WDA for the selected USB phone. Never opens Instagram."""
    with lock_device(device['udid']):return setup_attempt(device,team,lambda:_provision(device,team,notify))


def stop_controller_for_install(device,bundle):
    """Stop only the selected, installed bundle so Appium cannot reuse stale WDA code."""
    if str(device.get('ios','')).split('.')[0]=='16':return
    from urllib.parse import urlsplit,unquote
    apps=controller_apps(device['udid'],[bundle])
    paths={str(a.get('path','')).rstrip('/') for a in apps if a.get('bundle')==bundle and a.get('path')}
    if not paths:return
    processes=coredevice(['device','info','processes','--device',device['udid']]).get('runningProcesses',[])
    for process in processes:
        executable=unquote(urlsplit(process.get('executable','')).path)
        if any(executable.startswith(path+'/') for path in paths):
            pid=process.get('processIdentifier')
            if type(pid) is int and pid>0:coredevice(['device','process','terminate','--device',device['udid'],'--pid',str(pid)])

def _provision(device,team,notify):
    runtime=device_dir(device['udid']);runtime.mkdir(parents=True,exist_ok=True)
    with (runtime/'runner.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('请先停止这台手机的浏览任务，再重新签名。')
        fresh=next((d for d in scan_devices() if d['udid']==device['udid']),None)
        if not fresh:raise SetupError('connection','所选 iPhone 未通过 USB 连接，请重新检测。','连接')
        validate_device(fresh)
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
        target_bundle=(previous.get('bundle_id') if previous and previous.get('team_id')==team else payload['capabilities']['alwaysMatch']['appium:updatedWDABundleId']+'.xctrunner')
        stop_controller_for_install(fresh,target_bundle)
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
        notify('iOS 16 组件已安装，正在验证控制接口；安装图标不代表验证完成。' if legacy else '正在编译、签名并验证控制组件，不主动卸载旧控件。请保持手机解锁；若提示开发者未受信任，请在手机完成信任后重试。')
        value=call('POST','/session',build,timeout=360)
        value['server_url']=endpoint['url'];write_json(old,value)
        if not legacy:
            app=runtime/'DerivedData/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app'
            record=profile_record(app,device['udid'],team);record['controller']='modern'
        if previous:record['previous_expires_at']=previous.get('expires_at')
        state,_=expiry_state(record)
        if state=='expired':raise RuntimeError('签名已到期，请检查 Xcode 证书后重试。')
        write_json(runtime/'session-request.json',payload)
        write_json(runtime/'signing.json',record)
        write_json(runtime/'setup-state.json',{'status':'ready','message':'控制组件已连接验证。','updated_at':datetime.now(timezone.utc).isoformat()})
        (runtime/'pending-setup.json').unlink(missing_ok=True)
        notify('控制组件已安装并连接，签名有效期已读取。')
        return record

def verify_provision(device,notify=lambda message:None):
    runtime=device_dir(device['udid'])
    try:pending=json.loads((runtime/'pending-setup.json').read_text());team=pending['team_id']
    except (OSError,ValueError,KeyError,TypeError):
        record=signing_record(device['udid'])
        if not record or expiry_state(record)[0] in ('unknown','expired'):
            raise SetupError('not_installed','没有可重新验证的安装记录，请先安装控制组件。','安装')
        pending={'team_id':record['team_id'],'record':record};team=record['team_id']
    def job():
        with (runtime/'runner.lock').open('a') as guard:
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('请先停止这台手机的浏览任务，再验证控制组件。')
            fresh=next((d for d in scan_devices() if d['udid']==device['udid']),None)
            if not fresh:raise SetupError('connection','所选 iPhone 未通过 USB 连接，请重新检测。','连接')
            validate_device(fresh);record=pending['record'];legacy=record.get('controller')=='legacy16'
            derived=runtime/('LegacyDerivedData' if legacy else 'DerivedData')
            try:current=profile_record(derived/'Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app',device['udid'],team)
            except FileNotFoundError:raise SetupError('missing_build','本机缺少此组件的编译产物，无法直接验证。请在这台 Mac 上安装或重新签名一次。','安装') from None
            current['controller']=record.get('controller','modern')
            if expiry_state(current)[0] in ('unknown','expired'):raise SetupError('signature_invalid','签名已到期，请重新签名安装。','签名')
            if current['bundle_id']!=record['bundle_id']:raise RuntimeError('待验证组件已经改变，请重新安装。')
            if not controller_apps(device['udid'],[current['bundle_id']]):raise SetupError('not_installed','手机上未找到本次控制组件，因此不会出现对应开发者信任项。请先安装控制组件。','安装')
            from runtime_manager import ensure_server,ensure_legacy
            from runner import call
            endpoint=ensure_server(device['udid'],notify)
            old=runtime/'active-session.json'
            if old.exists():
                try:cached=json.loads(old.read_text())
                except (OSError,ValueError):cached={}
                caps=cached.get('capabilities',{})
                if cached and caps.get('udid',caps.get('appium:udid'))!=device['udid']:
                    raise RuntimeError('缓存设备标识不一致，未删除其他设备会话。')
                sid=cached.get('sessionId','')
                if cached.get('server_url')==endpoint['url'] and re.fullmatch('[A-Za-z0-9-]{1,80}',sid):
                    try:call('DELETE','/session/'+sid,timeout=8,server=endpoint['url'])
                    except Exception:pass
                old.unlink(missing_ok=True)
            payload=make_payload(fresh,team);caps=payload['capabilities']['alwaysMatch']
            caps['appium:updatedWDABundleId']=current['bundle_id'].removesuffix('.xctrunner');caps['appium:derivedDataPath']=str(derived)
            if legacy:caps['appium:webDriverAgentUrl']=ensure_legacy(device['udid'],current,notify)
            notify('正在复用已编译组件验证连接，不主动卸载组件或重建签名。')
            value=call('POST','/session',payload,timeout=240,server=endpoint['url']);value['server_url']=endpoint['url']
            write_json(runtime/'active-session.json',value);write_json(runtime/'session-request.json',payload);write_json(runtime/'signing.json',current)
            write_json(runtime/'setup-state.json',{'status':'ready','message':'控制组件已连接验证。','updated_at':datetime.now(timezone.utc).isoformat()})
            (runtime/'pending-setup.json').unlink(missing_ok=True);return current
    with lock_device(device['udid']):return setup_attempt(device,team,job)


def signing_record(udid):
    if not udid:return None
    path=device_dir(udid)/'signing.json'
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None

def close_other_sessions(keep=None):
    """Compatibility no-op: each device owns its sessions; never close other phones."""
    return 0
