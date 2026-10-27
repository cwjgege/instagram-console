"""Bounded LAN discovery and portable, credential-free WDA connection profiles."""
import hashlib
import ipaddress
import json
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit
import connections as cn
import device_manager as dm

FORMAT='instagram-console-connections'
PORTS=(18100,8100)
MAX_FILE=256*1024


def local_targets():
    """Probe active LAN interfaces, never VPN ranges or the whole private address space."""
    result=subprocess.run(['/sbin/ifconfig'],capture_output=True,text=True,timeout=5)
    if result.returncode:raise RuntimeError('无法读取本地网络，请检查 Wi-Fi 或有线网络连接。')
    targets=set();networks=[]
    for block in re.split(r'(?m)(?=^[A-Za-z][\w]*:)',result.stdout):
        if not re.match(r'(en\d+|bridge\d+):',block) or 'status: active' not in block:continue
        for address,mask in re.findall(r'\binet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-fA-F]+)',block):
            ip=ipaddress.IPv4Address(address)
            if not ip.is_private or ip.is_link_local or ip.is_loopback:continue
            net=ipaddress.IPv4Network((address,str(ipaddress.IPv4Address(int(mask,16)))),strict=False)
            # Keep discovery predictable even on a /16 office LAN. Imported addresses
            # and known endpoints are probed separately, including custom ports.
            if net.prefixlen<24:net=ipaddress.IPv4Network(f'{address}/24',strict=False)
            networks.append(str(net));targets.update(str(p) for p in net.hosts() if p!=ip)
    return sorted(targets)[:1024],sorted(set(networks))


def known_endpoints(profiles):
    endpoints={}
    for cfg in profiles:
        udid=cfg['device_udid']
        try:
            setting=cn.load(udid)
            runtime=json.loads((dm.device_dir(udid)/'network-runtime.json').read_text()) if setting['mode']=='wifi' else {}
            url=setting.get('wda_url') or runtime.get('url')
            if url:
                cn.validate_url(url)
                endpoints[url]={'udid':udid,'name':cfg.get('device_name','iPhone'),'fingerprint':setting.get('fingerprint',''),'preview_url':setting.get('preview_url') or runtime.get('preview_url',''),'identifier_kind':setting.get('identifier_kind','udid')}
        except (OSError,ValueError,KeyError):continue
    return endpoints


def preview_address(url):
    """Only offer common preview ports after seeing an actual MJPEG response."""
    from urllib.request import build_opener,ProxyHandler,Request
    host=urlsplit(url).hostname;port=urlsplit(url).port or 80
    ports={18100:(18101,),8100:(9100,)}.get(port,())
    for p in ports:
        candidate=f'http://[{host}]:{p}' if ':' in host else f'http://{host}:{p}'
        try:
            with build_opener(ProxyHandler({}),cn.NoRedirect()).open(Request(candidate+'/'),timeout=.7) as response:
                if response.headers.get('Content-Type','').lower().startswith('multipart/x-mixed-replace'):return candidate
        except (OSError,ValueError,RuntimeError):pass
    return ''


def discover_services(profiles=(),notify=lambda _:None,stop=None):
    hosts,networks=local_targets();known=known_endpoints(profiles)
    candidates=list(known)+[f'http://{host}:{port}' for host in hosts for port in PORTS]
    candidates=list(dict.fromkeys(candidates));found={};done=0
    notify('正在检查 '+('、'.join(networks) or '已保存地址')+' 的 WDA 服务…')
    def probe(url):
        if stop is not None and stop.is_set():return None
        u=urlsplit(url)
        try:
            # A short TCP check avoids spending HTTP timeouts on every LAN host.
            with socket.create_connection((u.hostname,u.port or 80),timeout=.3):pass
            info=cn.inspect(url)
            hint=known.get(url,{})
            # A changed endpoint must be shown as a new service, never inherit a binding.
            if hint.get('fingerprint') and hint['fingerprint']!=info['fingerprint']:hint={}
            return {**info,**{k:v for k,v in hint.items() if k!='fingerprint'},'wda_url':url,
                    'preview_url':hint.get('preview_url') or preview_address(url)}
        except (OSError,ValueError,RuntimeError):return None
    with ThreadPoolExecutor(max_workers=32) as pool:
        futures=[pool.submit(probe,url) for url in candidates]
        for future in as_completed(futures):
            done+=1
            if stop is not None and stop.is_set():
                for f in futures:f.cancel()
                break
            item=future.result()
            if item:
                key=item['fingerprint']
                if key not in found or item.get('udid'):found[key]=item
                notify(f'已发现 {len(found)} 台可用设备，正在完成局域网检查…')
            elif done%128==0:notify(f'已检查 {done}/{len(candidates)} 个地址与端口，发现 {len(found)} 台设备…')
    return {'devices':list(found.values()),'networks':networks,'checked':done}


def normalize_device(value):
    if not isinstance(value,dict):raise ValueError('连接文件中的设备格式无效。')
    url=cn.validate_url(value.get('wda_url',''))
    pin=value.get('fingerprint')
    if not isinstance(pin,str) or not pin or len(pin)>160:raise ValueError('连接文件缺少设备指纹，无法验证原手机。')
    udid=value.get('udid') or hashlib.sha256(pin.encode()).hexdigest()[:32]
    if not isinstance(udid,str):raise ValueError('设备标识无效。')
    dm.device_dir(udid)
    name=value.get('name','iPhone')
    if not isinstance(name,str) or not name.strip() or len(name)>160 or any(ord(c)<32 for c in name):raise ValueError('设备名称无效。')
    preview=value.get('preview_url','')
    if not isinstance(preview,str):raise ValueError('预览地址格式无效。')
    if preview:preview=cn.validate_url(preview)
    kind=value.get('identifier_kind','udid' if value.get('udid') else 'wda')
    if kind not in ('udid','wda'):raise ValueError('设备标识类型无效。')
    return {'udid':udid,'name':name.strip(),'wda_url':url,'preview_url':preview,'fingerprint':pin,
            'identifier_kind':kind}


def read_package(path):
    path=Path(path)
    with path.open('rb') as f:raw=f.read(MAX_FILE+1)
    if len(raw)>MAX_FILE:raise ValueError('连接文件过大，请选择本软件导出的 JSON 文件。')
    try:value=json.loads(raw)
    except (ValueError,UnicodeError):raise ValueError('连接文件不是有效 JSON。') from None
    if not isinstance(value,dict) or value.get('format')!=FORMAT or type(value.get('version')) is not int or value['version']!=1:raise ValueError('不支持的连接配置格式或版本。')
    devices=value.get('devices')
    if not isinstance(devices,list) or not 1<=len(devices)<=100:raise ValueError('连接文件应包含 1 至 100 台设备。')
    rows=[normalize_device(d) for d in devices]
    if len({d['udid'] for d in rows})!=len(rows) or len({d['fingerprint'] for d in rows})!=len(rows):raise ValueError('连接文件存在重复设备。')
    return rows


def export_package(profiles):
    rows=[]
    for url,hint in known_endpoints(profiles).items():
        if hint.get('fingerprint'):rows.append(normalize_device({**hint,'wda_url':url}))
    if not rows:raise ValueError('没有可导出的已验证无线连接。请先验证连接；USB 配置不包含可迁移的 WDA 地址。')
    return {'format':FORMAT,'version':1,'devices':rows}


def enroll(store,descriptor,running=lambda _:False):
    """Verify before writing; imports always connect an existing service, never sign/start."""
    item=normalize_device(descriptor)
    info=cn.checked(item['wda_url'],item['fingerprint'])
    profiles=store.profiles()
    for cfg in profiles:
        try:old=cn.load(cfg['device_udid'])
        except ValueError:continue
        if old.get('fingerprint')==item['fingerprint']:
            item['udid']=cfg['device_udid'];item['identifier_kind']=old.get('identifier_kind','udid');break
    udid=item['udid']
    if running(udid):raise ValueError('这台设备正在运行，请先停止再导入或更换连接。')
    from app_paths import lock_device
    with lock_device(udid):
        old=cn.load(udid)
        if old.get('fingerprint') and old['fingerprint']!=item['fingerprint']:raise ValueError('目标设备档案已绑定另一台手机，原配置保持不变。')
        # Preserve this Mac's startup management when re-importing its own Wi-Fi device.
        setting={**old} if old.get('mode')=='wifi' else {'mode':'remote'}
        setting.update(fingerprint=info['fingerprint'],verified_ios=info['ios'],identifier_kind=item['identifier_kind'])
        if setting['mode']=='remote':setting.update(wda_url=item['wda_url'],preview_url=item['preview_url'])
        profile={'udid':udid,'name':item['name'],'ios':info['ios'],'mode':setting['mode']}
        cfg_path=store.path(udid);connection_path=dm.device_dir(udid)/'connection.json';runtime_path=dm.device_dir(udid)/'network-runtime.json'
        paths=(cfg_path,connection_path,runtime_path);before={p:p.read_bytes() if p.exists() else None for p in paths}
        try:
            store.add(profile,explicit=True);cn.save(udid,setting)
            dm.write_json(runtime_path,{**info,'preview_url':item['preview_url']})
        except Exception:
            for path,raw in before.items():
                if raw is None:path.unlink(missing_ok=True)
                else:path.write_bytes(raw)
            raise
    return profile


def import_devices(store,items,running=lambda _:False,notify=lambda _:None,stop=None):
    saved=[];failed=[];discovered=None
    for item in items:
        if stop is not None and stop.is_set():break
        try:
            try:cn.checked(item['wda_url'],item['fingerprint'])
            except (OSError,ValueError,RuntimeError):
                notify('原地址不可用或不匹配，正在按设备指纹重新发现…')
                if discovered is None:discovered=discover_services(store.profiles(),notify,stop)['devices']
                found=next((d for d in discovered if d['fingerprint']==item['fingerprint']),None)
                if not found:raise RuntimeError('未发现原手机。请确认 WDA 已运行、网络互通；跨地区请检查导出地址或 Tailscale 路由。')
                item={**item,'wda_url':found['wda_url'],'preview_url':found.get('preview_url','')}
            if stop is not None and stop.is_set():break
            saved.append(enroll(store,item,running))
        except Exception as exc:failed.append(item['name']+'：'+str(exc))
    return {'saved':saved,'failed':failed}
