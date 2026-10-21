"""Explicit per-device transports. Network requests never use system HTTP proxies."""
import ipaddress,json,re,subprocess,threading,time,plistlib,fcntl
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener,ProxyHandler,Request,HTTPRedirectHandler

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise RuntimeError('网络 WDA 不允许 HTTP 重定向，请填写实际私网地址。')

MODES={'usb':'USB','wifi':'本地 Wi-Fi','remote':'远程已有 WDA'}
_PROCESSES={};_LOCK=threading.RLock()

def load(udid):
    import device_manager as dm
    path=dm.device_dir(udid)/'connection.json'
    if not path.exists():return {'mode':'usb'}
    try:value=json.loads(path.read_text())
    except (OSError,ValueError):raise ValueError('连接配置无法读取，请重新保存连接方式。')
    validate(value);return value

def save(udid,setting):
    import device_manager as dm
    validate(setting);dm.DATA.mkdir(parents=True,exist_ok=True)
    with (dm.DATA/'connection-bindings.lock').open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        pin=setting.get('fingerprint')
        if pin:
            for path in (dm.DATA/'devices').glob('*/connection.json'):
                if path.parent.name==udid:continue
                try:other=json.loads(path.read_text())
                except (OSError,ValueError):continue
                if other.get('fingerprint')==pin:raise RuntimeError('这台 WDA 手机已经绑定到另一个设备档案，请使用原档案，避免重复任务。')
        dm.write_json(dm.device_dir(udid)/'connection.json',setting)

def validate(cfg):
    if not isinstance(cfg,dict) or cfg.get('mode') not in MODES:raise ValueError('请选择 USB、本地 Wi-Fi 或远程已有 WDA。')
    if cfg['mode']!='usb' and (not isinstance(cfg.get('fingerprint'),str) or not cfg['fingerprint']):raise ValueError('请先验证并绑定这台手机的 WDA。')
    if cfg['mode']=='remote':
        validate_url(cfg.get('wda_url',''))
        if cfg.get('preview_url'):validate_url(cfg['preview_url'])
    return cfg

def validate_url(url):
    try:
        u=urlsplit(url);port=u.port
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):raise ValueError()
        if port is not None and not 1<=port<=65535:raise ValueError()
        host=u.hostname
        try:
            ip=ipaddress.ip_address(host)
            if not(ip.is_private or ip.is_loopback or ip in ipaddress.ip_network('100.64.0.0/10')) or ip.is_unspecified or ip.is_multicast:raise ValueError()
        except ValueError:
            if not (host.endswith(('.local','.ts.net')) or host=='localhost'):raise ValueError()
    except (ValueError,TypeError):raise ValueError('请输入可信局域网或 Tailscale 的 WDA 根地址，例如 http://100.64.1.2:8100；不支持公网地址、账号密码或 URL 路径。') from None
    return url.rstrip('/')

def get(url,path,timeout=4):
    validate_url(url)
    op=build_opener(ProxyHandler({}),NoRedirect())
    with op.open(Request(url+path),timeout=timeout) as response:
        raw=response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:raise RuntimeError('WDA 返回内容过大。')
        result=json.loads(raw)
    if not isinstance(result,dict):raise RuntimeError('地址没有返回 WDA JSON 对象。')
    return result.get('value',result)

def inspect(url):
    status=get(url,'/status')
    if not isinstance(status,dict) or status.get('ready') is not True or not status.get('build',{}).get('productBundleIdentifier'):raise RuntimeError('地址可访问，但不是已就绪的 WDA。')
    version=status.get('os',{}).get('version','')
    if not isinstance(version,str) or not re.fullmatch(r'\d+(?:\.\d+){0,2}',version):raise RuntimeError('WDA 未返回可识别的 iOS 版本。')
    info=get(url,'/wda/device/info')
    if not isinstance(info,dict):raise RuntimeError('WDA 未返回设备信息。')
    identity=info.get('uuid','')
    if not identity or identity=='unknown':raise RuntimeError('组件未返回设备指纹，无法安全绑定，请更新 WDA 后重试。')
    return {'fingerprint':identity,'name':info.get('name','iPhone'),'ios':status.get('os',{}).get('version',''),'bundle':status['build']['productBundleIdentifier'],'url':url}

def checked(url,pin):
    info=inspect(url)
    if not pin or info['fingerprint']!=pin:raise RuntimeError('WDA 设备指纹与已绑定手机不一致，已停止；请检查地址并重新验证绑定。')
    return info

def scan_network():
    import device_manager as dm
    result=dm.coredevice(['list','devices']);rows=[]
    for d in result.get('devices',[]):
        c=d.get('connectionProperties',{});h=d.get('hardwareProperties',{});p=d.get('deviceProperties',{})
        if h.get('deviceType')!='iPhone' or c.get('pairingState')!='paired' or c.get('transportType') not in ('wired','localNetwork'):continue
        rows.append({'udid':h.get('udid',''),'identifier':d['identifier'],'name':p.get('name','iPhone'),'model':h.get('marketingName','iPhone'),'ios':p.get('osVersionNumber','未知'),'paired':True,'developer':p.get('developerModeStatus')=='enabled','transport':c.get('transportType'),'network':True})
    return rows

def discover():
    import device_manager as dm
    wired=dm.scan_devices();seen={d['udid'] for d in wired}
    try:network=scan_network()
    except Exception:network=[]
    return wired+[d for d in network if d['udid'] not in seen]

def remote_device(cfg,setting):
    row={'udid':cfg['device_udid'],'name':cfg.get('device_name','iPhone'),'ios':cfg.get('device_ios',''),'transport':'remote'}
    try:
        checked(setting['wda_url'],setting.get('fingerprint'))
        return {**row,'paired':True,'developer':True}
    except Exception as exc:return {**row,'paired':False,'developer':False,'connection_error':type(exc).__name__+': '+str(exc)[:1000]}

def probe_remotes(profiles):
    """Read-only refresh of saved WDA endpoints; never discover, start or install anything."""
    rows=[]
    for cfg in profiles:
        try:setting=load(cfg['device_udid'])
        except ValueError:continue
        if setting['mode']=='remote':rows.append(remote_device(cfg,setting))
    return rows

def fleet_devices(profiles):
    import device_manager as dm
    rows=discover();by_id={d['udid']:d for d in rows}
    result=[]
    for cfg in profiles:
        udid=cfg['device_udid']
        try:setting=load(udid)
        except ValueError:continue
        mode=setting['mode'];d=by_id.get(udid)
        if mode=='remote':
            result.append(remote_device(cfg,setting))
        elif d and (mode=='wifi' or not d.get('network')):result.append(d)
    # Only automatically add physical USB phones. Network phones require explicit selection.
    known={d['udid'] for d in result}
    result.extend(d for d in rows if not d.get('network') and d['udid'] not in known)
    return result

def derived_path(udid):
    import device_manager as dm
    runtime=dm.device_dir(udid)
    try:
        request=json.loads((runtime/'session-request.json').read_text());caps=request['capabilities']['alwaysMatch']
        if caps['appium:udid']!=udid:raise RuntimeError('编译产物设备标识不一致。')
        return Path(caps['appium:derivedDataPath'])
    except (OSError,ValueError,KeyError):return runtime/'DerivedData'

def stop_device(udid):
    with _LOCK:process=_PROCESSES.pop(udid,None)
    if process and process.poll() is None:
        import os,signal
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass

def stop_all():
    for udid in list(_PROCESSES):stop_device(udid)

def record_connection_failure(udid,trace,error):
    """Keep bounded transport evidence without collecting app contents or proxy credentials."""
    import device_manager as dm
    evidence={**trace,'timestamp':time.time(),'error':str(error)[:2000],'routes':[]}
    for url in list(dict.fromkeys(trace.get('candidates',[])))[-3:]:
        try:
            validate_url(url);host=urlsplit(url).hostname
            # route only accepts literal IPs here; never invoke a shell or resolve arbitrary names.
            ip=ipaddress.ip_address(host.split('%')[0])
            args=['/sbin/route','-n','get']+(['-inet6'] if ip.version==6 else [])+[host]
            result=subprocess.run(args,capture_output=True,text=True,timeout=2)
            route={'host':host,'returncode':result.returncode}
            for line in result.stdout.splitlines():
                key,sep,value=line.strip().partition(':')
                if sep and key in ('interface','gateway','destination'):route[key]=value.strip()
            evidence['routes'].append(route)
        except (OSError,ValueError,subprocess.TimeoutExpired):continue
    path=dm.device_dir(udid)/'connection-diagnostic.json'
    dm.write_json(path,evidence)
    return path

def ensure_wifi(udid,notify=lambda s:None,stop=None,setting_override=None):
    from runner import Halt
    trace={'stage':'preflight','candidates':[]}
    try:return _ensure_wifi(udid,notify,stop,setting_override,trace)
    except Halt:
        stop_device(udid);raise
    except Exception as exc:
        stop_device(udid)
        # A diagnostic write failure must not replace the original connection error.
        try:path=record_connection_failure(udid,trace,exc)
        except Exception:raise exc
        raise RuntimeError(str(exc)+'\n连接诊断：'+str(path)) from exc

def _ensure_wifi(udid,notify,stop,setting_override,trace):
    import device_manager as dm
    from runner import Halt
    def check():
        if stop is not None and stop.is_set():raise Halt('已手动停止')
    check();setting=setting_override or load(udid)
    if setting.get('mode')=='remote':
        trace.update(stage='remote_endpoint',candidates=[setting['wda_url']])
        info=checked(setting['wda_url'],setting.get('fingerprint'))
        dm.write_json(dm.device_dir(udid)/'network-runtime.json',{**info,'preview_url':setting.get('preview_url','')});return setting['wda_url']
    record=dm.signing_record(udid)
    if not record or record.get('controller')=='legacy16':raise RuntimeError('本地无线启动目前支持已完成本机签名的 iOS 17 及以上手机；iOS 16 请保留 USB，或使用远程已有 WDA 模式。')
    if dm.expiry_state(record)[0] in ('unknown','expired'):raise RuntimeError('签名无效或到期，请连接 USB 重新签名。')
    details=dm.coredevice(['device','info','details','--device',udid]);h=details.get('hardwareProperties',{});c=details.get('connectionProperties',{});p=details.get('deviceProperties',{})
    trace.update(transport=c.get('transportType'),paired=c.get('pairingState'),ios=p.get('osVersionNumber'),developer=p.get('developerModeStatus'))
    if h.get('udid')!=udid or c.get('pairingState')!='paired':raise RuntimeError('未能验证原手机的开发配对，请首次通过 USB 完成配对。')
    if p.get('developerModeStatus')!='enabled':raise RuntimeError('请先开启手机开发者模式。')
    try:major=int(p.get('osVersionNumber','').split('.')[0])
    except ValueError:raise RuntimeError('未能读取手机系统版本，请重新检测。') from None
    if major<17:raise RuntimeError('iOS 16 本地无线自动启动尚未适配，请使用 USB 或远程已有 WDA 模式。')
    derived=derived_path(udid);runs=sorted((derived/'Build/Products').glob('*.xctestrun'),key=lambda f:f.stat().st_mtime,reverse=True)
    env={}
    def read_env(value):
        if isinstance(value,dict):
            if 'TestBundlePath' in value:env.update(value.get('EnvironmentVariables',{}))
            for child in value.values():read_env(child)
        elif isinstance(value,list):
            for child in value:read_env(child)
    if runs:read_env(plistlib.loads(runs[0].read_bytes()))
    port=int(env.get('USE_PORT',8100));mjpeg=int(env.get('MJPEG_SERVER_PORT',9100))
    if not 1<=port<=65535 or not 1<=mjpeg<=65535:raise RuntimeError('测试产物端口配置无效，请重新安装组件。')
    ip=c.get('tunnelIPAddress')
    if not ip:raise RuntimeError('苹果无线开发隧道未建立，请确认同一局域网、IPv6、解锁及首次配对；Tailscale 不能自动代替此步骤。')
    url=f'http://[{ip}]:{port}' if ':' in ip else f'http://{ip}:{port}'
    trace.update(stage='existing_endpoint',candidates=[url],wda_port=port,mjpeg_port=mjpeg)
    runtime=dm.device_dir(udid)
    # A running XCTest may advertise a LAN address different from Apple's tunnel.
    # Reuse it only after verifying the existing device binding again.
    if setting.get('fingerprint'):
        try:
            cached=json.loads((runtime/'network-runtime.json').read_text())
            trace['candidates'].append(cached['url'])
            info=checked(cached['url'],setting['fingerprint'])
            dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':cached.get('preview_url','')})
            return info['url']
        except (OSError,ValueError,KeyError):pass
    try:
        info=inspect(url)
        if setting.get('fingerprint') and info['fingerprint']!=setting['fingerprint']:raise RuntimeError('无线设备指纹不一致，已停止。')
        dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':url.rsplit(':',1)[0]+':'+str(mjpeg)});return url
    except (OSError,ValueError):pass
    # Launch the installed, signed runner without Xcode's IDE test handshake or reinstall.
    bundle=record.get('bundle_id','')
    if not re.fullmatch(r'com\.igconsole\.[A-Za-z0-9_.-]+\.xctrunner',bundle):raise RuntimeError('签名记录中的控制组件标识无效，请通过设备向导确认。')
    launch_env={'USE_PORT':str(port),'MJPEG_SERVER_PORT':str(mjpeg),'WDA_PRODUCT_BUNDLE_IDENTIFIER':bundle,
                'DYLD_FRAMEWORK_PATH':'/System/Developer/Library/Frameworks','DYLD_LIBRARY_PATH':'/System/Developer/usr/lib'}
    with _LOCK:
        trace['stage']='preinstalled_startup'
        process=_PROCESSES.get(udid)
        log_file=runtime/'wireless-wda.log'
        offset=log_file.stat().st_size if log_file.exists() else 0
        if process is None or process.poll() is not None:
            notify('正在直接启动已安装控制组件，不重新安装或签名；随后会短暂打开手机设置…')
            with log_file.open('ab') as log:
                process=subprocess.Popen([str(dm.xcode_directory()/'usr/bin/devicectl'),'device','process','launch','--device',udid,'--timeout','120','--console','--terminate-existing','--environment-variables',json.dumps(launch_env),bundle],env=dm.child_env(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            process._igconsole_log_offset=offset;process._igconsole_backgrounded=False
            _PROCESSES[udid]=process
        else:offset=getattr(process,'_igconsole_log_offset',offset)
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        check()
        with log_file.open('rb') as source:
            source.seek(offset);recent=source.read(2*1024*1024).decode(errors='replace')
        # A preinstalled runner starts its server once it moves to the background.
        if 'Launched application' in recent and not getattr(process,'_igconsole_backgrounded',False):
            trace['stage']='background_runner'
            result=subprocess.run([str(dm.xcode_directory()/'usr/bin/devicectl'),'device','process','launch','--device',udid,'--timeout','15','com.apple.Preferences'],env=dm.child_env(),capture_output=True,text=True,timeout=20)
            with log_file.open('a') as log:log.write('\nBackground transition:\n'+(result.stdout+result.stderr)[-12000:]+'\n')
            if result.returncode:raise RuntimeError('组件已启动，但切换到后台失败，请保持手机解锁。详情见 wireless-wda.log。')
            process._igconsole_backgrounded=True
        try:
            # devicectl owns the startup connection. Do not concurrently query CoreDevice
            # while it enables the developer image; use the runner's fresh LAN URL instead.
            url=f'http://[{ip}]:{port}' if ':' in ip else f'http://{ip}:{port}'
            candidates=[url]
            advertised=re.findall(r'ServerURLHere->(https?://[^<\s]+)<-ServerURLHere',recent)
            candidates=advertised[-1:]+candidates
            trace['candidates']=candidates
            if advertised:trace['stage']='wda_endpoint'
            last_error=None
            for candidate in candidates:
                try:info=inspect(candidate);url=candidate;break
                except OSError as exc:last_error=exc
            else:raise last_error or OSError('WDA 未返回可用地址')
            if info.get('bundle')!=bundle:raise RuntimeError('返回服务的组件标识与当前手机签名记录不一致，已停止。')
            if setting.get('fingerprint') and info['fingerprint']!=setting['fingerprint']:raise RuntimeError('无线设备指纹不一致，已停止。')
            dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':url.rsplit(':',1)[0]+':'+str(mjpeg)});return url
        except (OSError,ValueError):pass
        if process.poll() is not None:
            if 'is not installed' in recent:
                trace['stage']='component_missing'
                raise RuntimeError('手机未安装本机签名记录对应的控制组件（'+bundle+'）。请在“连接与签名”恢复该组件；若换过签名团队，需要核对实际安装标识。无需因此重复创建证书。')
            if trace['stage']=='wda_endpoint':raise RuntimeError('组件已报告启动，但控制端口未通过验证；请检查局域网直连，详情见 wireless-wda.log。')
            raise RuntimeError('已安装组件启动失败；请查看 wireless-wda.log 中的具体原因，不会自动覆盖安装或创建证书。')
        if stop is not None:stop.wait(1)
        else:time.sleep(1)
    stop_device(udid)
    if trace['stage']=='wda_endpoint':raise RuntimeError('组件已报告启动，但 Mac 无法验证其控制端口。请检查局域网设备隔离和直连路由；不需要因此重复创建证书。详情见 wireless-wda.log。')
    raise RuntimeError('无线 WDA 启动超时。请保持解锁，检查 wireless-wda.log；必要时重新插 USB 恢复开发环境。')

def network_payload(cfg,url):
    return {'capabilities':{'alwaysMatch':{'platformName':'iOS','appium:automationName':'XCUITest','appium:udid':cfg['device_udid'],'appium:deviceName':cfg.get('device_name','iPhone'),'appium:platformVersion':cfg['device_ios'],'appium:webDriverAgentUrl':url,'appium:wdaRemotePort':urlsplit(url).port or 8100,'appium:noReset':True,'appium:shouldTerminateApp':False,'appium:skipLogCapture':True,'appium:useNewWDA':False,'appium:newCommandTimeout':300},'firstMatch':[{}]}}
