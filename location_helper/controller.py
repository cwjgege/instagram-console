# SPDX-License-Identifier: GPL-3.0-or-later
"""Selected-device XCTest and loopback TCP relay over an isolated userspace tunnel."""
import asyncio,json,re,sys
from contextlib import suppress

def validate(cfg):
    if not re.fullmatch(r'(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{16}|[a-fA-F0-9]{40})',cfg.get('udid','')):raise ValueError('Invalid device')
    if not re.fullmatch(r'com\.igconsole\.[A-Za-z0-9_.-]+\.xctrunner',cfg.get('bundle','')):raise ValueError('Invalid signed runner')
    ports=[cfg.get(k) for k in ('local_wda','local_mjpeg','remote_wda','remote_mjpeg')]
    if any(type(p) is not int or not 1024<p<65536 for p in ports) or ports[0]==ports[1]:raise ValueError('Invalid relay ports')
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

async def serve(cfg,emit):
    validate(cfg)
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    from pymobiledevice3.services.dvt.testmanaged.xcuitest import TestConfig,XCUITestService
    async with UserspaceRsdTunnel(serial=cfg['udid'],autopair=False,remotepairing_fallback=False) as rsd:
        if rsd.udid.replace('-','').lower()!=cfg['udid'].replace('-','').lower():raise RuntimeError('Paired device mismatch')
        testcfg=await TestConfig.create_for(rsd,cfg['bundle'])
        testcfg.runner_app_env={'USE_PORT':str(cfg['remote_wda']),'MJPEG_SERVER_PORT':str(cfg['remote_mjpeg']),'WDA_PRODUCT_BUNDLE_IDENTIFIER':cfg['bundle']}
        task=asyncio.create_task(XCUITestService(rsd).run(testcfg))
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
            deadline=asyncio.get_running_loop().time()+60
            while True:
                if task.done():task.result();raise RuntimeError('XCTest exited before ready')
                try:
                    status=await asyncio.wait_for(response(rsd,cfg['remote_wda'],'/status'),3)
                    info=await asyncio.wait_for(response(rsd,cfg['remote_wda'],'/wda/device/info'),3)
                    if status.get('ready') is not True or status.get('build',{}).get('productBundleIdentifier') not in {cfg['bundle'],cfg['bundle'].removesuffix('.xctrunner')} or not info.get('uuid') or info['uuid']=='unknown':raise RuntimeError('Unverified signed WDA')
                    break
                except Exception:
                    if asyncio.get_running_loop().time()>=deadline:raise
                    await asyncio.sleep(.5)
            for local,remote in ((cfg['local_wda'],cfg['remote_wda']),(cfg['local_mjpeg'],cfg['remote_mjpeg'])):
                servers.append(await asyncio.start_server(lambda r,w,p=remote:relay(r,w,p),'127.0.0.1',local))
            emit('controller_ready',udid=cfg['udid'],fingerprint=info['uuid'],bundle=status['build']['productBundleIdentifier'],ios=status['os']['version'])
            await task
        finally:
            for server in servers:server.close();await server.wait_closed()
            for client in list(clients):client.cancel()
            await asyncio.gather(*clients,return_exceptions=True)
            task.cancel()
            with suppress(asyncio.CancelledError):await task
