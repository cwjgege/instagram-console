"""Paired-device relay; existing WDA first, signed Xcode launch only when needed."""
import asyncio,json,re,sys
from contextlib import suppress
from pathlib import Path

def validate(cfg):
    if not re.fullmatch(r'(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{16}|[a-fA-F0-9]{40})',cfg.get('udid','')):raise ValueError('Invalid device')
    if not re.fullmatch(r'com\.igconsole\.[A-Za-z0-9_.-]+\.xctrunner',cfg.get('bundle','')):raise ValueError('Invalid signed runner')
    ports=[cfg.get(k) for k in ('local_wda','local_mjpeg','remote_wda','remote_mjpeg')]
    if any(type(p) is not int or not 1024<p<65536 for p in ports) or ports[0]==ports[1] or ports[2]==ports[3]:raise ValueError('Invalid relay ports')
    return cfg

async def response(rsd,port,route):
    async with await rsd.create_service_connection(port) as conn:
        await conn.sendall(('GET '+route+' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n').encode())
        raw=b''
        while b'\r\n\r\n' not in raw:
            chunk=await conn.recv_any(65536)
            if not chunk:raise RuntimeError('Incomplete WDA response')
            raw+=chunk
            if len(raw)>131072:raise RuntimeError('Oversized WDA response')
        header,body=raw.split(b'\r\n\r\n',1)
        if not header.startswith(b'HTTP/1.1 200'):raise RuntimeError('WDA not ready')
        length=int(next(line.split(b':',1)[1].strip() for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')))
        if not 0<length<=131072:raise RuntimeError('Oversized WDA body')
        while len(body)<length:
            chunk=await conn.recv_any(min(65536,length-len(body)))
            if not chunk:raise RuntimeError('Incomplete WDA body')
            body+=chunk
        return json.loads(body[:length])['value']

class IdentityMismatch(RuntimeError):pass

async def identity(rsd,cfg):
    status=await asyncio.wait_for(response(rsd,cfg['remote_wda'],'/status'),3)
    info=await asyncio.wait_for(response(rsd,cfg['remote_wda'],'/wda/device/info'),3)
    if status.get('build',{}).get('productBundleIdentifier') not in {cfg['bundle'],cfg['bundle'].removesuffix('.xctrunner')}:raise IdentityMismatch('Paired WDA signing bundle differs from this device record')
    if status.get('ready') is not True or not info.get('uuid') or info['uuid']=='unknown':raise RuntimeError('Paired WDA not ready')
    return status,info

async def launch_standard(cfg):
    executable=Path(cfg.get('xcodebuild',''));spec=Path(cfg.get('xctestrun',''))
    if not executable.is_absolute() or not executable.is_file() or not str(executable).endswith('/usr/bin/xcodebuild') or not spec.is_absolute() or spec.suffix!='.xctestrun' or not spec.is_file():
        raise RuntimeError('没有可用的本机签名测试产物；请通过 USB 的连接与签名恢复此设备组件。'+cfg.get('launch_configuration_error',''))
    return await asyncio.create_subprocess_exec(str(executable),'test-without-building','-xctestrun',str(spec),'-destination','id='+cfg['udid'],'-destination-timeout','30','-parallel-testing-enabled','NO','-maximum-concurrent-test-device-destinations','1',stdin=asyncio.subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr)

async def ready(rsd,cfg,emit):
    """Return a verified identity plus an owned Xcode child, if one was necessary."""
    try:
        status,info=await identity(rsd,cfg);emit('controller_stage',stage='reuse_existing_wda');return status,info,None
    except IdentityMismatch:raise
    except Exception:pass
    emit('controller_stage',stage='standard_xcode_startup')
    child=await launch_standard(cfg)
    try:
        deadline=asyncio.get_running_loop().time()+60
        while True:
            if child.returncode is not None:raise RuntimeError('标准 Xcode 启动提前结束，退出码 '+str(child.returncode)+'；请查看 controller-wireless.log 中的 Xcode 错误。')
            try:status,info=await identity(rsd,cfg);return status,info,child
            except IdentityMismatch:raise
            except Exception as exc:
                if asyncio.get_running_loop().time()>=deadline:raise RuntimeError('标准 Xcode 启动后未读到就绪的控件：'+type(exc).__name__+': '+str(exc)[:500]) from exc
                await asyncio.sleep(.5)
    except BaseException:
        await stop_child(child);raise

async def stop_child(child):
    if child is None:return
    if child.returncode is None:
        with suppress(ProcessLookupError):child.terminate()
        try:await asyncio.wait_for(child.wait(),5)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):child.kill()
            await child.wait()
    else:await child.wait()

async def serve(cfg,emit):
    validate(cfg)
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    async with UserspaceRsdTunnel(serial=cfg['udid'],autopair=False,remotepairing_fallback=False) as rsd:
        if rsd.udid.replace('-','').lower()!=cfg['udid'].replace('-','').lower():raise IdentityMismatch('Paired device mismatch')
        status,info,child=await ready(rsd,cfg,emit)
        servers=[];clients=set()
        async def relay(reader,writer,remote):
            owner=asyncio.current_task();clients.add(owner)
            try:
                async with await rsd.create_service_connection(remote) as conn:
                    async def upload():
                        while chunk:=await reader.read(65536):await conn.sendall(chunk)
                    async def download():
                        while chunk:=await conn.recv_any(65536):writer.write(chunk);await writer.drain()
                    streams=[asyncio.create_task(upload()),asyncio.create_task(download())]
                    try:await asyncio.wait(streams,return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for stream in streams:stream.cancel()
                        await asyncio.gather(*streams,return_exceptions=True)
            except Exception:pass
            finally:
                clients.discard(owner);writer.close()
                with suppress(Exception):await writer.wait_closed()
        try:
            for local,remote in ((cfg['local_wda'],cfg['remote_wda']),(cfg['local_mjpeg'],cfg['remote_mjpeg'])):
                servers.append(await asyncio.start_server(lambda r,w,p=remote:relay(r,w,p),'127.0.0.1',local))
            emit('controller_ready',udid=cfg['udid'],fingerprint=info['uuid'],bundle=status['build']['productBundleIdentifier'],ios=status['os']['version'])
            failures=0
            while True:
                await asyncio.sleep(3)
                if child is not None and child.returncode is not None:raise RuntimeError('标准 Xcode 控制会话已结束，退出码 '+str(child.returncode))
                try:
                    _,current=await identity(rsd,cfg)
                    if current['uuid']!=info['uuid']:raise IdentityMismatch('WDA identity changed while relaying')
                    failures=0
                except IdentityMismatch:raise
                except Exception:
                    failures+=1
                    if failures>=3:raise RuntimeError('独立无线通道连续三次未能读回控件状态，需要重新连接。') from None
        finally:
            for server in servers:server.close();await server.wait_closed()
            for client in list(clients):client.cancel()
            await asyncio.gather(*clients,return_exceptions=True)
            await stop_child(child)
