"""Read fresh CoreLocation samples from the bound phone; never use simulated-location cache APIs."""
import json,math,re,threading,time
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,Request
import connections as cn,device_manager as dm
from location_manager import coordinates

class ReadbackError(RuntimeError):pass
class IdentityMismatch(ReadbackError):pass
class StaleLocation(ReadbackError):pass

READ_TIMEOUT=15
REFRESH_INTERVAL=60

def sample(value,pin,now=None):
    now=time.time() if now is None else now
    if not isinstance(value,dict) or value.get('locationSchema')!=1:raise ReadbackError('手机控件尚不支持带时间的定位读回，请在连接与签名中更新控制组件。')
    if not pin or value.get('deviceFingerprint')!=pin:raise IdentityMismatch('定位读回设备不匹配，已停止自动纠偏。')
    if value.get('locationServicesEnabled') is False:raise ReadbackError('手机定位服务未开启，无法确认当前位置。')
    if value.get('locationServicesEnabled') is not True:raise ReadbackError('手机定位服务状态尚未确认，等待下次读取。')
    if value.get('authorizationStatus')!=3:raise ReadbackError('请在手机定位服务中允许 WebDriverAgentRunner“始终”访问位置，并开启精确位置；授权前不自动纠偏。')
    if value.get('accuracyAuthorization')!=0:raise ReadbackError('手机未允许控件使用精确位置，未自动修改定位。')
    if value.get('valid') is not True:raise ReadbackError('尚未收到有效定位，正在等待新的位置样本。')
    try:
        lat,lon=coordinates(value.get('latitude'),value.get('longitude'))
        stamp=value['timestamp'];accuracy=value['horizontalAccuracy']
        if isinstance(stamp,bool) or isinstance(accuracy,bool):raise ValueError()
        stamp=float(stamp);accuracy=float(accuracy)
        if not math.isfinite(stamp) or not math.isfinite(accuracy) or accuracy<0 or accuracy>200:raise ValueError()
    except (KeyError,ValueError,TypeError):raise ReadbackError('定位数据或精度无效，未自动修改位置。') from None
    if now-stamp>20:raise StaleLocation('等待新的位置报告；上次样本已超过 20 秒，未据此自动改位置。')
    if now-stamp < -5:raise ReadbackError('手机位置时间晚于电脑，请校准手机和电脑时间后重试。')
    return {'latitude':lat,'longitude':lon,'timestamp':stamp,'accuracy':accuracy}

def distance_m(a,b):
    lat1,lon1=map(math.radians,a);lat2,lon2=map(math.radians,b)
    h=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8*2*math.asin(math.sqrt(min(1,max(0,h))))

class LocationReader:
    def __init__(self):self.bindings={};self.lock=threading.Lock();self.refreshed={};self.heartbeats={};self.recoveries={};self.cancel=None
    def session(self,udid,required=True):
        try:
            state=json.loads((dm.device_dir(udid)/'active-session.json').read_text());caps=state['capabilities'];sid=state['sessionId']
            if caps.get('udid',caps.get('appium:udid'))!=udid or not re.fullmatch('[A-Za-z0-9-]{1,80}',sid):raise IdentityMismatch('控制会话设备标识不匹配。')
            ports=json.loads((dm.DATA/'ports.json').read_text())[udid.lower()]
            root=f"http://127.0.0.1:{int(ports['appium'])}"
            actual=cn.get(root,'/igconsole/session/'+sid,timeout=3)
            if actual.get('udid',actual.get('appium:udid'))!=udid:raise IdentityMismatch('运行中的控制会话属于其他设备，未读取定位。')
            return root,sid,ports
        except ReadbackError:raise
        except Exception as exc:
            if required:raise ReadbackError('请先在此手机完成控制组件连接验证，再开启自动保持。') from exc
            return None
    def endpoint(self,udid):
        cfg=cn.load(udid)
        if cfg['mode']=='usb':
            try:
                state=json.loads((dm.device_dir(udid)/'active-session.json').read_text());caps=state['capabilities']
                actual=caps.get('udid',caps.get('appium:udid'))
                if actual!=udid:raise ValueError()
                ports=json.loads((dm.DATA/'ports.json').read_text())[udid.lower()]
                url=f"http://127.0.0.1:{ports['wda']}"
            except (OSError,KeyError,ValueError,TypeError):raise ReadbackError('请先在这台手机完成控制组件连接验证，再开启自动保持。') from None
            self.session(udid)  # Also refresh Appium's idle timer while the guard owns reads.
            with self.lock:old=self.bindings.get(udid)
            info=cn.checked(url,old[1]) if old and old[0]==url else cn.inspect(url)
            pin=info['fingerprint']
        else:
            try:url=cfg['wda_url'] if cfg['mode']=='remote' else json.loads((dm.device_dir(udid)/'network-runtime.json').read_text())['url']
            except (OSError,ValueError,KeyError,TypeError):raise ReadbackError('无线控制接口未就绪，请先验证本机 Wi-Fi 连接。') from None
            pin=cfg['fingerprint']
            try:cn.checked(url,pin)
            except cn.FingerprintMismatch:
                if cfg['mode']!='wifi':raise
                url=cn.ensure_wifi(udid);pin=cn.load(udid)['fingerprint'];cn.checked(url,pin)
            self.session(udid,required=False)
        with self.lock:self.bindings[udid]=(url,pin)
        return url,pin
    def prepare(self,udid,stop=None,notify=lambda text:None):
        if stop is not None and stop.is_set():raise ReadbackError('已停止连接准备。')
        try:return self.endpoint(udid)
        except IdentityMismatch:raise
        except Exception:
            record=dm.signing_record(udid)
            if not record or record.get('udid')!=udid or dm.expiry_state(record)[0] in ('unknown','expired'):raise ReadbackError('此手机没有有效的本机组件安装记录，请先完成签名安装。') from None
            mode=cn.load(udid)['mode']
            notify('正在连接已安装的控制组件，保留现有签名…')
            if mode=='usb':dm.verify_provision({'udid':udid},notify)
            elif mode=='wifi':cn.ensure_wifi(udid,notify,stop)
            else:raise ReadbackError('远程控制接口不可达，请先在远程 Mac 恢复该手机的控制服务。')
            if stop is not None and stop.is_set():raise ReadbackError('已停止连接准备。')
            return self.endpoint(udid)
    def request_permission(self,udid):
        from location_manager import validate_target
        validate_target(udid)
        record=dm.signing_record(udid)
        if not record or record.get('udid')!=udid:raise ReadbackError('请先完成此手机的控制组件签名与连接验证。')
        url,pin=self.prepare(udid)
        current=cn.get(url,'/wda/device/location',timeout=4,max_bytes=16384)
        if current.get('authorizationStatus')==3 and current.get('accuracyAuthorization')==0:return current
        dm.coredevice(['device','process','launch','--device',udid,record['bundle_id']])
        cn.checked(url,pin)
        return cn.get(url,'/wda/device/location',timeout=4,max_bytes=16384)
    def read_endpoint(self,udid):
        """Resolve locally; the location response itself must prove the pinned identity.

        Status/device-info snapshots queue behind XCTest searches. Requiring both on
        every tick used to time out before even asking CoreLocation for a sample.
        """
        cfg=cn.load(udid)
        if cfg['mode']=='usb':
            try:
                state=json.loads((dm.device_dir(udid)/'active-session.json').read_text())
                caps=state['capabilities']
                if caps.get('udid',caps.get('appium:udid'))!=udid:raise IdentityMismatch('控制会话设备标识不匹配。')
                ports=json.loads((dm.DATA/'ports.json').read_text())[udid.lower()]
                url=f"http://127.0.0.1:{int(ports['wda'])}"
            except ReadbackError:raise
            except (OSError,KeyError,ValueError,TypeError):raise ReadbackError('USB 控制接口未就绪，请先验证此设备连接。') from None
            with self.lock:old=self.bindings.get(udid)
            if not old or old[0]!=url:return self.endpoint(udid)
            pin=old[1]
        else:
            try:
                url=cfg['wda_url'] if cfg['mode']=='remote' else json.loads((dm.device_dir(udid)/'network-runtime.json').read_text())['url']
                pin=cfg['fingerprint']
                if not isinstance(pin,str) or not pin:raise ValueError()
            except (OSError,KeyError,ValueError,TypeError):raise ReadbackError('无线定位接口未就绪，等待控制连接恢复。') from None
        cn.validate_url(url)
        with self.lock:self.bindings[udid]=(url,pin)
        return url,pin
    def refresh(self,udid,url,pin):
        """Restart only CoreLocation updates, never the runner or simulated GPS."""
        now=time.monotonic()
        if now-self.refreshed.get(udid,-REFRESH_INTERVAL)<REFRESH_INTERVAL:return
        self.refreshed[udid]=now
        opener=build_opener(ProxyHandler({}),cn.NoRedirect())
        request=Request(url+'/wda/device/location/stop',data=json.dumps({'deviceFingerprint':pin}).encode(),headers={'Content-Type':'application/json'},method='POST')
        with opener.open(request,timeout=READ_TIMEOUT) as response:response.read(16384)
        # Re-create the reader immediately; the next tick waits for a genuine fix.
        value=cn.get(url,'/wda/device/location',timeout=READ_TIMEOUT,max_bytes=16384)
        if not isinstance(value,dict) or value.get('deviceFingerprint')!=pin:raise IdentityMismatch('定位读回设备不匹配，已停止自动纠偏。')
    def read(self,udid):
        url,pin=self.read_endpoint(udid)
        try:value=cn.get(url,'/wda/device/location',timeout=READ_TIMEOUT,max_bytes=16384)
        except OSError:
            if not self.recover(udid):raise
            url,pin=self.read_endpoint(udid)
            value=cn.get(url,'/wda/device/location',timeout=READ_TIMEOUT,max_bytes=16384)
        try:observed=sample(value,pin)
        except StaleLocation:
            self.refresh(udid,url,pin)
            raise
        # Keep an idle Appium session alive, but never make its slow response a
        # prerequisite for receiving an identity-checked CoreLocation sample.
        if time.monotonic()-self.heartbeats.get(udid,-60)>=60:
            self.heartbeats[udid]=time.monotonic();self.session(udid,required=False)
        return observed
    def recover(self,udid):
        """Reopen an idle phone's existing controller, never compete with a task."""
        now=time.monotonic()
        if now-self.recoveries.get(udid,-60)<60:return False
        self.recoveries[udid]=now
        if cn.load(udid)['mode']!='wifi':return False
        from app_paths import lock_device
        try:ownership=lock_device(udid)
        except RuntimeError:return False  # The browsing runner owns reconnects.
        try:
            if self.cancel is not None and self.cancel.is_set():return False
            self.prepare(udid,self.cancel)
            return True
        finally:ownership.close()
    def stop(self,udid):
        with self.lock:binding=self.bindings.pop(udid,None)
        if not binding:return
        url,pin=binding
        try:
            cn.checked(url,pin)
            opener=build_opener(ProxyHandler({}),cn.NoRedirect())
            request=Request(url+'/wda/device/location/stop',data=json.dumps({'deviceFingerprint':pin}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with opener.open(request,timeout=4) as response:response.read(16384)
        except Exception:pass  # Stopping a reader must not prevent restoring the phone's location.
