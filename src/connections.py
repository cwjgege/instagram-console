"""Explicit per-device transports. Network requests never use system HTTP proxies."""
import ipaddress,json,re,subprocess,threading,time,plistlib,fcntl
import hashlib
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener,ProxyHandler,Request,HTTPRedirectHandler

class FingerprintMismatch(RuntimeError):pass

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
    if isinstance(value,dict) and value.get('merged_into'):raise ValueError('此发现档案已合并到原来的已配对手机档案，请使用原手机档案。')
    validate(value);return value

def merged_target(udid):
    import device_manager as dm
    try:
        old=json.loads((dm.device_dir(udid)/'connection.json').read_text())
        if not isinstance(old,dict):return None
        target=old.get('merged_into')
        if not target:return None
        if not (dm.device_dir(target)/'config.json').is_file():return None
        current=load(target)
        if current.get('mode')=='wifi' and current.get('fingerprint')==old.get('previous_connection',{}).get('fingerprint'):return target
    except (OSError,ValueError,TypeError):pass
    return None

def save(udid,setting,merge_discovered_alias=False,expected_fingerprint=None,expected_setting=None,stop=None):
    import device_manager as dm
    validate(setting);dm.DATA.mkdir(parents=True,exist_ok=True)
    with (dm.DATA/'connection-bindings.lock').open('a') as guard, ExitStack() as locks:
        fcntl.flock(guard,fcntl.LOCK_EX)
        if expected_fingerprint is not None:
            current=load(udid)
            if current.get('mode')!='wifi' or current.get('fingerprint')!=expected_fingerprint:raise RuntimeError('连接绑定已被其他操作修改，未覆盖新配置。')
        if expected_setting is not None and load(udid)!=expected_setting:raise RuntimeError('连接配置已被其他操作修改，未覆盖新配置。')
        pin=setting.get('fingerprint');aliases=[]
        if pin:
            for path in (dm.DATA/'devices').glob('*/connection.json'):
                if path.parent.name==udid:continue
                try:other=json.loads(path.read_text())
                except (OSError,ValueError):continue
                if not isinstance(other,dict):continue
                if other.get('fingerprint')!=pin:continue
                # Only collapse a discovery-generated identity after the caller
                # has verified this physical phone's local signed XCTest server.
                if (merge_discovered_alias and setting['mode']=='wifi' and setting.get('identifier_kind','udid')=='udid'
                    and other.get('mode')=='remote' and other.get('identifier_kind')=='wda'
                    and path.parent.name==hashlib.sha256(pin.encode()).hexdigest()[:32]):
                    from app_paths import lock_device
                    locks.enter_context(lock_device(path.parent.name))
                    aliases.append((path,other));continue
                raise RuntimeError('这台 WDA 手机已经绑定到另一个设备档案，请使用原档案，避免重复任务。')
        if stop is not None and stop.is_set():
            from runner import Halt
            raise Halt('已手动停止')
        target=dm.device_dir(udid)/'connection.json'
        before={p:p.read_bytes() if p.exists() else None for p in [target]+[p for p,_ in aliases]}
        try:
            dm.write_json(target,setting)
            for path,old in aliases:dm.write_json(path,{'mode':'usb','merged_into':udid,'previous_connection':old})
        except Exception:
            for path,raw in before.items():
                if raw is None:path.unlink(missing_ok=True)
                else:path.write_bytes(raw)
            raise

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

def get(url,path,timeout=4,max_bytes=2*1024*1024):
    validate_url(url)
    op=build_opener(ProxyHandler({}),NoRedirect())
    with op.open(Request(url+path),timeout=timeout) as response:
        raw=response.read(max_bytes+1)
        if len(raw)>max_bytes:raise RuntimeError('WDA 返回内容过大。')
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
    if not pin or info['fingerprint']!=pin:raise FingerprintMismatch('WDA 设备指纹与已绑定手机不一致，已停止；请检查地址并重新验证绑定。')
    return info

def refresh_wifi_identity(udid,setting,url,info,port,stop=None,notify=lambda s:None):
    import device_manager as dm
    from runner import Halt
    def check():
        if stop is not None and stop.is_set():raise Halt('已手动停止')
    check();old=setting.get('fingerprint')
    if not old or old==info.get('fingerprint'):return
    if setting.get('mode')!='wifi' or setting.get('identifier_kind','udid')!='udid':raise FingerprintMismatch('无法确认本机配对设备，未自动更新指纹。')
    record=dm.signing_record(udid)
    if not record or record.get('udid')!=udid or dm.expiry_state(record)[0] in ('unknown','expired'):raise FingerprintMismatch('缺少此手机的有效本机签名记录，未自动更新指纹。')
    bundle=record.get('bundle_id','')
    if not re.fullmatch(r'com\.igconsole\.[A-Za-z0-9_.-]+\.xctrunner',bundle):raise FingerprintMismatch('本机签名组件标识无效，未自动更新指纹。')
    notify('控件指纹已变化，正在通过已配对设备编号核验同一台手机…')
    try:proof=dm.controller_identity(udid,port)
    except (OSError,RuntimeError,subprocess.SubprocessError) as exc:raise FingerprintMismatch('未能通过已配对手机核验指纹，原绑定保留：'+str(exc)[:200]) from exc
    check()
    allowed={bundle,bundle.removesuffix('.xctrunner')}
    if proof.get('udid')!=udid or proof.get('fingerprint')!=info.get('fingerprint') or not info.get('fingerprint') or proof.get('bundle') not in allowed or info.get('bundle') not in allowed:
        raise FingerprintMismatch('无线服务与已配对手机或本机签名控件不一致，未更新指纹。')
    # Re-read the same endpoint after the trusted-device proof; address reuse or
    # a service replacement during the probe must not authorize a different peer.
    current=checked(url,proof['fingerprint']);check()
    if current.get('bundle') not in allowed:raise FingerprintMismatch('核验期间控制组件发生变化，未更新指纹。')
    saved=load(udid)
    if saved.get('mode')!='wifi' or saved.get('fingerprint')!=old:raise FingerprintMismatch('连接配置已变化，未覆盖原绑定。')
    updated={**saved,'fingerprint':proof['fingerprint'],'verified_ios':current['ios']}
    check();save(udid,updated,expected_fingerprint=old,expected_setting=saved,stop=stop)
    setting['fingerprint']=proof['fingerprint']
    try:dm.write_json(dm.device_dir(udid)/'fingerprint-refresh.json',{'udid':udid,'previous_fingerprint':old,'fingerprint':proof['fingerprint'],'bundle':bundle,'verification':'paired-usbmux-wda','updated_at':time.time()})
    except OSError:pass
    notify('已通过配对设备编号与签名控件核验，自动更新指纹并保留原配置。')


def inspect_wifi_identity(udid,setting,url,port,stop=None,notify=lambda s:None):
    info=inspect(url)
    if setting.get('fingerprint') and info.get('fingerprint')!=setting['fingerprint']:
        refresh_wifi_identity(udid,setting,url,info,port,stop,notify)
    return info


def scan_network():
    import device_manager as dm
    result=dm.coredevice(['list','devices']);rows=[]
    for d in result.get('devices',[]):
        c=d.get('connectionProperties',{});h=d.get('hardwareProperties',{});p=d.get('deviceProperties',{})
        if h.get('deviceType')!='iPhone' or c.get('pairingState')!='paired' or c.get('transportType') not in ('wired','localNetwork'):continue
        rows.append({'udid':h.get('udid',''),'identifier':d['identifier'],'name':p.get('name','iPhone'),'model':h.get('marketingName','iPhone'),'ios':p.get('osVersionNumber','未知'),'paired':True,'developer':dm.developer_mode(p.get('developerModeStatus')),'transport':c.get('transportType'),'network':True})
    return dm.resolve_developer_modes(rows)

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
    except Exception as exc:
        message=type(exc).__name__+': '+str(exc)[:1000]
        if isinstance(exc,ConnectionRefusedError) or isinstance(getattr(exc,'reason',None),ConnectionRefusedError):
            message+='\n保存地址的控制端口拒绝连接，请核对手机当前 IP 并恢复 WDA。若手机已在本机签名配对，请从原来的手机档案选择“本地 Wi-Fi”验证启动；“远程已有 WDA”不会启动组件。'
        return {**row,'paired':False,'developer':False,'connection_error':message}

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

def saved_wifi_endpoints(udid,pin):
    """Retain verified LAN candidates when discovery aliases are merged."""
    import device_manager as dm
    if not pin:return []
    candidates=[]
    try:candidates.append(json.loads((dm.device_dir(udid)/'network-runtime.json').read_text()))
    except (OSError,ValueError):pass
    for path in (dm.DATA/'devices').glob('*/connection.json'):
        try:
            value=json.loads(path.read_text());old=value.get('previous_connection',{})
            if value.get('merged_into')==udid and old.get('fingerprint')==pin and old.get('mode')=='remote':
                candidates.append({'url':old['wda_url'],'preview_url':old.get('preview_url','')})
        except (OSError,ValueError,KeyError,AttributeError,TypeError):continue
    valid=[];seen=set()
    for value in candidates:
        try:
            url=validate_url(value['url'])
            if url in seen:continue
            seen.add(url);valid.append({**value,'url':url})
        except (ValueError,KeyError,TypeError):continue
    # A temporary Apple development tunnel can pass preflight and then disappear.
    # Prefer a previously bound IPv4 endpoint, but recheck its fingerprint first.
    return sorted(valid,key=lambda value:':' in (urlsplit(value['url']).hostname or ''))

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
    from app_paths import APP_VERSION
    evidence={**trace,'app_version':APP_VERSION,'timestamp':time.time(),'error':str(error)[:2000],'routes':[]}
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

def prepare_wireless_xctestrun(derived,runtime,bundle,port,mjpeg):
    """Use this Mac's signed test host, with paths independent of spec location."""
    products=Path(derived)/'Build/Products'
    runs=sorted(products.glob('*.xctestrun'),key=lambda p:p.stat().st_mtime,reverse=True)
    if not runs:raise RuntimeError('本机缺少已签名的 XCTest 测试产物。请通过 USB 在本机完成一次组件安装或验证，再使用 Wi-Fi；不需要重复创建证书。')
    def targets(value):
        if isinstance(value,dict):
            if 'TestBundlePath' in value:yield value
            else:
                for child in value.values():yield from targets(child)
        elif isinstance(value,list):
            for child in value:yield from targets(child)
    def relocate(value,root):
        if isinstance(value,str):return value.replace('__TESTROOT__',str(root))
        if isinstance(value,dict):return {k:relocate(v,root) for k,v in value.items()}
        if isinstance(value,list):return [relocate(v,root) for v in value]
        return value
    for run in runs:
        try:value=relocate(plistlib.loads(run.read_bytes()),run.parent.resolve())
        except (OSError,ValueError,plistlib.InvalidFileException):continue
        rows=list(targets(value))
        if not rows or any(row.get('TestHostBundleIdentifier')!=bundle for row in rows):continue
        for row in rows:
            host=Path(row.get('TestHostPath',''))
            try:identity=plistlib.loads((host/'Info.plist').read_bytes()).get('CFBundleIdentifier')
            except (OSError,ValueError,plistlib.InvalidFileException):break
            if identity!=bundle:break
            test=Path(row['TestBundlePath'].replace('__TESTHOST__',str(host)))
            if not test.exists():break
            row.setdefault('EnvironmentVariables',{}).update(USE_PORT=str(port),MJPEG_SERVER_PORT=str(mjpeg),WDA_PRODUCT_BUNDLE_IDENTIFIER=bundle)
        else:
            path=Path(runtime)/'wireless.xctestrun'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(plistlib.dumps(value));return path
    raise RuntimeError('本机 XCTest 产物与当前签名组件不匹配或文件缺失。请通过 USB 的“连接与签名”恢复该手机组件，不会启动其他签名组件。')

def ensure_wifi(udid,notify=lambda s:None,stop=None,setting_override=None):
    from runner import Halt
    trace={'stage':'preflight','candidates':[]}
    try:
        url=_ensure_wifi(udid,notify,stop,setting_override,trace)
        # Preserve candidate failures even if a later development tunnel succeeds.
        try:
            import device_manager as dm
            from app_paths import APP_VERSION
            dm.write_json(dm.device_dir(udid)/'connection-attempt.json',{**trace,'selected_url':url,'timestamp':time.time(),'app_version':APP_VERSION})
        except OSError:pass
        return url
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
    state={'udid':udid,'paired':True,'developer':dm.developer_mode(p.get('developerModeStatus'))}
    dm.resolve_developer_modes([state]);trace['verified_developer']=state['developer']
    if state['developer'] is None:raise RuntimeError('尚未确认手机开发者模式状态，请解锁后重新检测；这不代表开发者模式未开启。')
    if state['developer'] is False:raise RuntimeError('请先开启手机开发者模式。')
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
    ip=c.get('tunnelIPAddress') or ''
    url=(f'http://[{ip}]:{port}' if ':' in ip else f'http://{ip}:{port}') if ip else None
    trace.update(stage='existing_endpoint',candidates=[url] if url else [],wda_port=port,mjpeg_port=mjpeg)
    runtime=dm.device_dir(udid)
    # A running XCTest may advertise a LAN address different from Apple's tunnel.
    # Reuse it only after verifying the existing device binding again.
    if setting.get('fingerprint'):
        for cached in saved_wifi_endpoints(udid,setting['fingerprint']):
            try:
                trace['candidates'].append(cached['url'])
                info=inspect_wifi_identity(udid,setting,cached['url'],port,stop,notify)
                dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':cached.get('preview_url','')})
                return info['url']
            except (OSError,ValueError,KeyError,FingerprintMismatch) as exc:
                trace.setdefault('endpoint_errors',[]).append({'url':cached['url'],'error':type(exc).__name__+': '+str(exc)[:1000]})
    try:
        if not url:raise OSError('开发隧道尚未报告地址，交由 XCTest 建立开发连接。')
        info=inspect_wifi_identity(udid,setting,url,port,stop,notify)
        dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':url.rsplit(':',1)[0]+':'+str(mjpeg)});return url
    except (OSError,ValueError):pass
    # Standard XCTest orchestration is required on affected Xcode 27/DDI stacks.
    # Launching the runner as an ordinary app (even --no-activate) can fail with
    # XCTest 10300 before the HTTP server exists. Reuse only this phone's signed
    # build, without building, changing teams or requesting provisioning updates.
    bundle=record.get('bundle_id','')
    if not re.fullmatch(r'com\.igconsole\.[A-Za-z0-9_.-]+\.xctrunner',bundle):raise RuntimeError('签名记录中的控制组件标识无效，请通过设备向导确认。')
    runtime=dm.device_dir(udid)
    with _LOCK:
        trace.update(stage='xctest_startup',startup_method='xcodebuild-test-without-building')
        process=_PROCESSES.get(udid)
        log_file=runtime/'wireless-wda.log'
        offset=log_file.stat().st_size if log_file.exists() else 0
        if process is None or process.poll() is not None:
            spec=prepare_wireless_xctestrun(derived,runtime,bundle,port,mjpeg)
            notify('正在通过 XCTest 启动已有签名组件，不重新编译或创建证书；请保持手机解锁并完成手机上的自动化验证。')
            args=[str(dm.xcode_directory()/'usr/bin/xcodebuild'),'test-without-building',
                  '-xctestrun',str(spec),'-destination','id='+udid,'-destination-timeout','30',
                  '-parallel-testing-enabled','NO','-maximum-concurrent-test-device-destinations','1']
            with log_file.open('ab') as log:
                process=subprocess.Popen(args,env=dm.child_env(),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            process._igconsole_log_offset=offset
            _PROCESSES[udid]=process
        else:offset=getattr(process,'_igconsole_log_offset',offset)
    deadline=time.monotonic()+150
    while time.monotonic()<deadline:
        check()
        with log_file.open('rb') as source:
            source.seek(offset);recent=source.read(2*1024*1024).decode(errors='replace')
        # Only fresh output from this attempt can supply a LAN address or error.
        try:
            # Do not concurrently query CoreDevice while XCTest prepares the device.
            # Check the development tunnel and the runner's fresh LAN URL instead.
            url=(f'http://[{ip}]:{port}' if ':' in ip else f'http://{ip}:{port}') if ip else None
            candidates=[url] if url else []
            advertised=re.findall(r'ServerURLHere->(https?://[^<\s]+)<-ServerURLHere',recent)
            candidates=advertised[-1:]+candidates
            trace['candidates']=candidates
            if advertised:trace['stage']='wda_endpoint'
            last_error=None
            for candidate in candidates:
                try:info=inspect(candidate);url=candidate;break
                except OSError as exc:last_error=exc
            else:raise last_error or OSError('WDA 未返回可用地址')
            if info.get('bundle') not in {bundle,bundle.removesuffix('.xctrunner')}:raise RuntimeError('返回服务的组件标识与当前手机签名记录不一致，已停止。')
            if setting.get('fingerprint') and info['fingerprint']!=setting['fingerprint']:refresh_wifi_identity(udid,setting,url,info,port,stop,notify)
            dm.write_json(runtime/'network-runtime.json',{**info,'preview_url':url.rsplit(':',1)[0]+':'+str(mjpeg)});return url
        except (OSError,ValueError):pass
        if process.poll() is not None:
            if re.search(r'for reason: Locked|BSErrorCodeDescription\s*=\s*Locked',recent):
                trace['stage']='device_locked'
                raise RuntimeError('iOS 拒绝启动组件：手机处于锁定状态。请在手机输入锁屏密码、保持亮屏后重新验证；不需要重装或创建证书。')
            if 'Failed to background test runner' in recent:
                trace['stage']='background_runner_failed'
                raise RuntimeError('iOS 测试组件未能进入后台（XCTest 10300）。标准 XCTest 启动仍未完成，请保留本次日志；用 USB 在 Xcode 中验证开发环境，不要重复创建证书。')
            if 'Timed out while enabling automation mode' in recent:
                trace['stage']='automation_authorization'
                raise RuntimeError('iOS 启用自动化超时。即使“UI 自动化”开关已开启，仍需检查手机上的密码或自动化验证提示；请完成后重试。详情见 wireless-wda.log。')
            if 'Unable to find a device matching' in recent:
                trace['stage']='xctest_destination_missing'
                raise RuntimeError('Xcode 未能连接所选 iPhone。请保持手机解锁，在 Xcode 的 Devices and Simulators 确认网络连接；必要时插 USB 恢复开发连接，再重试。')
            if any(value in recent for value in ('Test host path does not exist','Failed to load test bundle','The test bundle could not be found')):
                trace['stage']='xctest_build_invalid'
                raise RuntimeError('本机测试产物缺失或与当前 Xcode 不兼容，请通过 USB 的“连接与签名”恢复组件；不会自动创建证书。')
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
