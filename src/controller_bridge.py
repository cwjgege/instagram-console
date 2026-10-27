"""Own one isolated, paired wireless controller relay per phone."""
import json,subprocess,time
from pathlib import Path
import device_manager as dm
import connections as cn
from location_manager import worker_command
from runtime_manager import endpoints,ExistingProcess
from runner import Halt

def preferred(udid):
    try:return json.loads((dm.device_dir(udid)/'controller-strategy.json').read_text()).get('strategy')=='userspace'
    except (OSError,ValueError,AttributeError):return False

def accept(udid,record,proof,url,setting,stop):
    if proof.get('udid')!=udid or proof.get('bundle') not in {record['bundle_id'],record['bundle_id'].removesuffix('.xctrunner')} or not proof.get('fingerprint') or proof['fingerprint']=='unknown':raise RuntimeError('无线备用通道设备或签名核验不一致。')
    info=cn.checked(url,proof['fingerprint'])
    if info.get('bundle')!=proof['bundle']:raise RuntimeError('无线备用通道控件已变化。')
    if stop is not None and stop.is_set():raise Halt('已手动停止')
    old=cn.load(udid)
    if old.get('mode')=='wifi' and old.get('fingerprint')!=proof['fingerprint']:
        cn.save(udid,{**old,'fingerprint':proof['fingerprint'],'verified_ios':info['ios']},expected_fingerprint=old['fingerprint'],expected_setting=old,stop=stop)
    setting['fingerprint']=proof['fingerprint']
    return info

def ensure(udid,setting,notify=lambda s:None,stop=None):
    record=dm.signing_record(udid)
    if not record or record.get('udid')!=udid or dm.expiry_state(record)[0] in ('unknown','expired'):raise RuntimeError('此手机缺少有效的本机签名，请先完成设备配置。')
    ports=endpoints(udid);base=dm.device_dir(udid);url=f"http://127.0.0.1:{ports['wda']}"
    with cn._LOCK:
        process=cn._PROCESSES.get(udid)
        if process is None:
            try:
                pid=json.loads((base/'controller-process.json').read_text())['pid']
                command=subprocess.check_output(['/bin/ps','-p',str(pid),'-o','command='],text=True).strip()
                expected=' '.join(worker_command()+['--run-controller',str(base/'controller-request.json')])
                if command==expected:
                    process=ExistingProcess(pid);cn._PROCESSES[udid]=process
            except (OSError,ValueError,KeyError,subprocess.SubprocessError):pass
        if process is not None and process.poll() is None:
            try:
                proof=json.loads((base/'controller-proof.json').read_text());info=accept(udid,record,proof,url,setting,stop)
                return url
            except (OSError,ValueError,RuntimeError):cn.stop_device(udid)
        request=base/'controller-request.json';dm.write_json(request,{'udid':udid,'bundle':record['bundle_id'],'local_wda':ports['wda'],'local_mjpeg':ports['mjpeg'],'remote_wda':18100,'remote_mjpeg':18101})
        notify('正在通过已配对的独立无线通道启动控件，不重新签名…')
        log=base/'controller-wireless.log'
        with log.open('wb') as output:
            process=subprocess.Popen(worker_command()+['--run-controller',str(request)],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        cn._PROCESSES[udid]=process
        dm.write_json(base/'controller-process.json',{'pid':process.pid})
    deadline=time.monotonic()+80
    while time.monotonic()<deadline:
        if stop is not None and stop.is_set():cn.stop_device(udid);raise Halt('已手动停止')
        for line in log.read_text(errors='replace').splitlines()[-30:]:
            try:proof=json.loads(line)
            except ValueError:continue
            if proof.get('event')!='controller_ready':continue
            info=accept(udid,record,proof,url,setting,stop)
            dm.write_json(base/'controller-proof.json',proof)
            dm.write_json(base/'network-runtime.json',{**info,'preview_url':f"http://127.0.0.1:{ports['mjpeg']}"})
            dm.write_json(base/'controller-strategy.json',{'strategy':'userspace'})
            notify('已通过配对设备编号和签名核验无线通道。');return url
        if process.poll() is not None:raise RuntimeError('独立无线通道启动失败，请查看 controller-wireless.log；请检查普通无线配对及手机解锁状态。')
        time.sleep(.25)
    cn.stop_device(udid);raise RuntimeError('独立无线控件启动超时，未操作手机页面。')
