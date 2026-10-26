"""Prepare trusted wireless pairing on USB, then prove both channels after unplugging."""
import json,os,subprocess,time,threading
import device_manager as dm
import connections as cn
from location_manager import validate_target,worker_command
from app_paths import lock_device

def state(udid):
    try:
        value=json.loads((dm.device_dir(udid)/'wireless-setup.json').read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def save(udid,status,message,**extra):
    value={'status':status,'message':message,'updated_at':time.time(),**extra}
    dm.write_json(dm.device_dir(udid)/'wireless-setup.json',value);return value

def wired(udid):return udid.replace('-','').lower() in dm.usb_serials()

def helper(udid,action):
    validate_target(udid)
    env={k:v for k,v in os.environ.items() if not k.startswith(('PYMOBILEDEVICE3_','USBMUXD_','_PYI_')) and k not in ('PYTHONHOME','PYTHONPATH','DYLD_LIBRARY_PATH')}
    result=subprocess.run(worker_command()+[action,udid],env=env,capture_output=True,text=True,timeout=45)
    try:value=json.loads(result.stdout.splitlines()[-1])
    except (ValueError,IndexError):raise RuntimeError('无线准备组件未返回有效结果，请重新连接 USB 后重试。') from None
    expected='wireless_prepared' if action=='--prepare-wireless' else 'wireless_checked'
    if result.returncode or value.get('event')!=expected:raise RuntimeError(value.get('error','无线通道验证未通过。'))
    if str(value.get('udid','')).replace('-','').lower()!=udid.replace('-','').lower():raise RuntimeError('无线准备组件返回了其他设备，未保存设置。')
    if action=='--prepare-wireless' and value.get('enabled') is not True:raise RuntimeError('尚未确认无线配对已开启。')
    if action=='--check-wireless' and value.get('transport')!='Network':raise RuntimeError('未验证到无线定位通道。')
    return value

def prepare(udid,notify=lambda text:None):
    try:
        with lock_device(udid):
            if not wired(udid):raise RuntimeError('首次准备无线连接需插入这台手机的 USB 数据线。')
            notify('正在通过现有 USB 配对开启无线连接，不重新签名…')
            helper(udid,'--prepare-wireless')
        return save(udid,'enabled','无线配对已开启。请拔掉 USB，保持手机解锁和同一 Wi-Fi，软件将自动验证。')
    except Exception as exc:
        save(udid,'failed','无线准备未完成：'+str(exc)[:500]);raise

def _verify(udid,stop=None,notify=lambda text:None):
    stop=stop or threading.Event()
    def check():
        if stop.is_set():raise RuntimeError('已取消无线验证，连接方式未切换。')
        if wired(udid):raise RuntimeError('请先拔掉 USB，才能验证实际 Wi-Fi 连接。')
    try:
        check();notify('正在验证拔线后的无线定位通道…');helper(udid,'--check-wireless');check()
        notify('正在验证无线控制组件…')
        old=cn.load(udid);setting={'mode':'wifi','identifier_kind':'udid'}
        if old.get('mode')=='wifi' and old.get('fingerprint'):setting['fingerprint']=old['fingerprint']
        url=cn.ensure_wifi(udid,notify,stop,setting_override=setting);info=cn.inspect(url)
        check()
        if setting.get('fingerprint') and info['fingerprint']!=setting['fingerprint']:raise RuntimeError('设备指纹发生变化，未保存无线连接。')
        record=dm.signing_record(udid)
        if not record or info['bundle'] not in {record.get('bundle_id'),str(record.get('bundle_id','')).removesuffix('.xctrunner')}:raise RuntimeError('无线控件与本机签名记录不匹配，未切换连接。')
        setting.update(fingerprint=info['fingerprint'],verified_ios=info['ios'],wda_url='',preview_url='')
        cn.save(udid,setting,merge_discovered_alias=True)
        return save(udid,'verified','Wi-Fi 控制和定位通道已验证，已切换为本地 Wi-Fi。此次检查没有修改手机定位。')
    except Exception as exc:
        save(udid,'failed','无线验证未完成：'+str(exc)[:500]+'；原连接方式保留，可插回 USB 重试。');raise

def verify(udid,stop=None,notify=lambda text:None):
    with lock_device(udid):return _verify(udid,stop,notify)
