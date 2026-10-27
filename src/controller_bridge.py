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

def startup_failure(log,returncode):
    import re
    try:
        with Path(log).open('rb') as stream:
            stream.seek(0,2);stream.seek(max(0,stream.tell()-65536));lines=stream.read().decode('utf-8','replace').splitlines()
    except OSError:lines=[]
    detail=''
    for line in reversed(lines):
        try:value=json.loads(line)
        except ValueError:value={}
        if isinstance(value,dict) and value.get('event')=='error' and value.get('error'):
            detail=str(value['error'])[:1200];break
        if re.match(r'^[\w.]+(?:Error|Exception):',line):detail=line[:1200];break
    detail=detail or '未收到可用的控件核验结果，请查看 controller-wireless.log。'
    if 'ConnectionTerminatedError' in detail:detail+='；XCTest 测试进程在完成启动前断开，请保留本次启动日志；这不代表开发者模式未开启。'
    return RuntimeError('独立无线通道启动失败（退出码 '+str(returncode)+'）：'+detail)

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
                request_cfg=json.loads((base/'controller-request.json').read_text())
                if request_cfg.get('launch_mode')!='standard':raise RuntimeError('旧版无线进程需要按新版方式重新建立。')
                proof=json.loads((base/'controller-proof.json').read_text());info=accept(udid,record,proof,url,setting,stop)
                return url
            except (OSError,ValueError,RuntimeError):cn.stop_device(udid)
        request=base/'controller-request.json'
        cfg={'udid':udid,'bundle':record['bundle_id'],'local_wda':ports['wda'],'local_mjpeg':ports['mjpeg'],'remote_wda':8100,'remote_mjpeg':9100,'launch_mode':'standard'}
        try:
            derived=cn.derived_path(udid);cfg['remote_wda'],cfg['remote_mjpeg']=cn.wireless_ports(derived)
            cfg['xctestrun']=str(cn.prepare_wireless_xctestrun(derived,base,record['bundle_id'],cfg['remote_wda'],cfg['remote_mjpeg']))
            cfg['xcodebuild']=str(dm.xcode_directory()/'usr/bin/xcodebuild')
        except (OSError,ValueError,RuntimeError) as exc:cfg['launch_configuration_error']=str(exc)[:500]
        dm.write_json(request,cfg)
        notify('正在核验独立配对通道中的已有控件；需要启动时使用本机 Xcode，不重新签名…')
        log=base/'controller-wireless.log'
        with log.open('wb') as output:
            process=subprocess.Popen(worker_command()+['--run-controller',str(request)],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        cn._PROCESSES[udid]=process
        dm.write_json(base/'controller-process.json',{'pid':process.pid})
    deadline=time.monotonic()+120
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
        if process.poll() is not None:raise startup_failure(log,process.returncode)
        time.sleep(.25)
    cn.stop_device(udid);raise RuntimeError('独立无线控件启动验证超时，请查看 controller-wireless.log 中的启动记录。')
