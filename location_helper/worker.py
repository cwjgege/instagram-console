# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate device-service process. NDJSON location control and paired wireless relay."""
import asyncio,json,math,os,re,sys
from contextlib import AsyncExitStack
from importlib.metadata import version

PIN='11.44.0'
def emit(event,**data):print(json.dumps({'event':event,**data},ensure_ascii=False),flush=True)
def normalize(value):return str(value).replace('-','').lower()
def validate(request):
    udid=request.get('udid','')
    if not re.fullmatch(r'(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{16}|[a-fA-F0-9]{40})',udid):raise ValueError('需要真实 iPhone UDID，请先通过 USB 添加设备。')
    action=request.get('action')
    if action not in ('set','clear'):raise ValueError('不支持的定位操作。')
    if action=='set':
        for key,limit in (('latitude',90),('longitude',180)):
            value=request.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -limit<=value<=limit:raise ValueError('经纬度范围无效。')
    return udid,action

async def serve(reader,backend=None):
    initial=json.loads(await reader.readline());udid,action=validate(initial)
    if version('pymobiledevice3')!=PIN:raise RuntimeError('定位后端版本不匹配。')
    if backend is None:backend=DeviceBackend()
    async with backend:
        await asyncio.wait_for(backend.connect(udid),40)
        request=initial
        while True:
            selected,action=validate(request)
            if normalize(selected)!=normalize(udid):raise ValueError('定位会话不能切换设备。')
            await asyncio.wait_for(backend.verify(udid),8)
            if action=='clear':
                await asyncio.wait_for(backend.clear(),10)
                # DVT clear is fire-and-forget. Do not claim GPS verification.
                emit('clear_sent',udid=udid);return
            await asyncio.wait_for(backend.set(request['latitude'],request['longitude']),12)
            emit('applied',udid=udid,latitude=request['latitude'],longitude=request['longitude'])
            pending=asyncio.create_task(reader.readline())
            try:
                while True:
                    done,_=await asyncio.wait({pending},timeout=10)
                    if done:break
                    await asyncio.wait_for(backend.verify(udid),8)
                    emit('connected',udid=udid)
                raw=pending.result()
            finally:
                if not pending.done():pending.cancel()
            if not raw:
                try:
                    await asyncio.wait_for(backend.clear(),6);emit('clear_sent',udid=udid)
                except Exception:emit('restore_unknown',udid=udid)
                return
            request=json.loads(raw)

class DeviceBackend:
    def __init__(self,connection_type=None):self.connection_type=connection_type
    async def __aenter__(self):self.stack=AsyncExitStack();await self.stack.__aenter__();return self
    async def __aexit__(self,*args):return await self.stack.__aexit__(*args)
    async def connect(self,udid):
        from pymobiledevice3.lockdown import create_using_usbmux
        self.lockdown=await create_using_usbmux(serial=udid,autopair=False,connection_type=self.connection_type)
        self.stack.push_async_callback(self.lockdown.close)
        actual=await self.lockdown.get_value(key='UniqueDeviceID')
        if normalize(actual)!=normalize(udid):raise RuntimeError('设备编号不匹配，未修改定位。')
        ios=tuple(int(n) for n in self.lockdown.product_version.split('.')[:2])
        self.provider=self.lockdown
        if ios>=(17,0):
            if ios<(17,4):raise RuntimeError('本版定位暂支持 iOS 17.4 及以上，以及 iOS 16；此系统需单独验证连接方式。')
            from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
            # Never suspend remoted or fall back to the shared native tunnel.
            self.provider=await self.stack.enter_async_context(UserspaceRsdTunnel(serial=udid,autopair=False,remotepairing_fallback=False))
            if normalize(self.provider.udid)!=normalize(udid):raise RuntimeError('定位隧道设备不匹配，未修改定位。')
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
            self.dvt=await self.stack.enter_async_context(DvtProvider(self.provider))
            self.location=await self.stack.enter_async_context(LocationSimulation(self.dvt))
        elif ios>=(16,0):
            from pymobiledevice3.services.simulate_location import DtSimulateLocation
            self.location=DtSimulateLocation(self.lockdown)
        else:raise RuntimeError('本版定位需要 iOS 16 或以上。')
    async def verify(self,udid):
        actual=await self.lockdown.get_value(key='UniqueDeviceID')
        if normalize(actual)!=normalize(udid):raise RuntimeError('设备连接已变化，已停止定位操作。')
        if hasattr(self,'dvt') and self.dvt.dtx._closed:raise RuntimeError('定位开发服务连接已断开。')
    async def set(self,lat,lon):await self.location.set(lat,lon)
    async def clear(self):await self.location.clear()

async def wireless_command(action,udid):
    validate({'action':'clear','udid':udid})
    if action=='--prepare-wireless':
        from pymobiledevice3.lockdown import create_using_usbmux
        client=await create_using_usbmux(serial=udid,autopair=False,connection_type='USB')
        try:
            if normalize(client.udid)!=normalize(udid) or not client.paired:raise RuntimeError('设备配对不匹配，未修改无线设置。')
            before=await client.get_enable_wifi_connections()
            if not before:await client.set_enable_wifi_connections(True)
            if await client.get_enable_wifi_connections() is not True:raise RuntimeError('手机尚未确认开启无线配对。')
            emit('wireless_prepared',udid=udid,enabled=True,changed=not before)
        finally:await client.close()
    else:
        async with DeviceBackend(connection_type='Network') as backend:
            await backend.connect(udid);await backend.verify(udid)
            emit('wireless_checked',udid=udid,transport='Network')

async def main():
    import certifi
    os.environ['SSL_CERT_FILE']=certifi.where()
    if len(sys.argv)==2 and sys.argv[1]=='--self-test':
        # Import every device path without creating a connection.
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
        from pymobiledevice3.services.simulate_location import DtSimulateLocation
        assert version('pymobiledevice3')==PIN
        emit('self_test',version=PIN);return
    if len(sys.argv)==3 and sys.argv[1]=='--run-controller':
        from controller import serve as controller_serve
        from pathlib import Path
        await controller_serve(json.loads(Path(sys.argv[2]).read_text()),emit);return
    if len(sys.argv)==3 and sys.argv[1] in ('--prepare-wireless','--check-wireless'):
        try:await asyncio.wait_for(wireless_command(sys.argv[1],sys.argv[2]),40)
        except Exception as exc:emit('error',error=type(exc).__name__+': '+str(exc)[:600]);raise SystemExit(1)
        return
    reader=asyncio.StreamReader(limit=8192)
    await asyncio.get_running_loop().connect_read_pipe(lambda:asyncio.StreamReaderProtocol(reader),sys.stdin)
    try:await serve(reader)
    except Exception as exc:
        emit('error',error=type(exc).__name__+': '+str(exc)[:1200]);raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())
