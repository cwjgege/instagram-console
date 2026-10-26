"""Per-device fixed GPS requests, isolated from automation processes."""
import json,math,os,re,subprocess,sys,threading,time
from pathlib import Path
from app_paths import ASSETS,FROZEN
import device_manager as dm

PIN='11.44.0'
NEW_YORK=(40.7128,-74.0060)
def coordinates(lat,lon):
    try:
        if isinstance(lat,bool) or isinstance(lon,bool):raise ValueError()
        lat,lon=float(lat),float(lon)
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError()
    except (ValueError,TypeError):raise ValueError('请输入有效经纬度：纬度 -90～90，经度 -180～180。') from None
    return lat,lon

def validate_target(udid):
    if not re.fullmatch(r'(?:[a-fA-F0-9]{8}-[a-fA-F0-9]{16}|[a-fA-F0-9]{40})',udid):raise ValueError('此档案没有真实设备编号，请先通过 USB 添加手机，再使用定位。')
    import connections
    cfg=connections.load(udid)
    if cfg.get('identifier_kind')=='wda':raise ValueError('已有 WDA 地址不能代替定位配对，请先通过 USB 添加手机。')
    return udid

def worker_command():
    if FROZEN:
        path=ASSETS/'location-backend/ig-location-helper'
        if not path.is_file():raise RuntimeError('定位后端未打包，请安装包含定位组件的新版应用。')
        return [str(path)]
    python=os.environ.get('IG_LOCATION_PYTHON',sys.executable)
    return [python,str(Path(__file__).resolve().parents[1]/'location_helper/worker.py')]

class LocationManager:
    def __init__(self):
        self.lock=threading.RLock();self.sessions={};self.states={};self.closing=False
        from location_guard import LocationGuard
        self.guard=LocationGuard(self)
    def _path(self,udid):return dm.device_dir(udid)/'location.json'
    def snapshot(self,udid):
        with self.lock:
            if udid in self.states:return dict(self.states[udid])
            try:old=json.loads(self._path(udid).read_text())
            except (OSError,ValueError):old={}
            if not isinstance(old,dict):old={}
            return {'guard_enabled':False,'guard_status':'off','phase':'idle','message':'尚未设置模拟定位。' if not old else '上次定位请求已记录，当前手机位置尚未确认。','latitude':old.get('latitude'),'longitude':old.get('longitude')}
    def _state(self,udid,phase,message,**extra):
        previous=self.states.get(udid,{})
        state={**previous,**extra,'phase':phase,'message':message,'updated_at':time.time()}
        self.states[udid]=state
        try:dm.write_json(self._path(udid),state)
        except OSError:pass
    def apply(self,udid,lat,lon,keep=False):
        lat,lon=coordinates(lat,lon);validate_target(udid)
        with self.lock:
            if self.states.get(udid,{}).get('phase')=='busy':raise RuntimeError('此设备的定位请求正在处理中。')
            self.guard.stop(udid)
            self._request(udid,{'action':'set','udid':udid,'latitude':lat,'longitude':lon})
            if keep:self.guard.start(udid,(lat,lon))
    def stop_guard(self,udid):self.guard.stop(udid)
    def clear(self,udid):
        validate_target(udid)
        with self.lock:
            self.guard.stop(udid);self._request(udid,{'action':'clear','udid':udid})
    def _request(self,udid,request):
        with self.lock:
            if self.closing:raise RuntimeError('软件正在退出，请稍候。')
            if self.states.get(udid,{}).get('phase')=='busy':raise RuntimeError('此设备的定位请求正在处理中。')
            session=self.sessions.get(udid)
            if session and session['process'].poll() is not None:self.sessions.pop(udid,None);session=None
            if session is None:
                command=worker_command()
                env={k:v for k,v in os.environ.items() if not k.startswith(('PYMOBILEDEVICE3_','USBMUXD_','_PYI_'))}
                env['PYTHONUNBUFFERED']='1'
                if FROZEN:
                    for key in ('PYTHONHOME','PYTHONPATH','DYLD_LIBRARY_PATH'):env.pop(key,None)
                directory=dm.device_dir(udid);directory.mkdir(parents=True,exist_ok=True)
                log=(directory/'location-backend.log').open('a')
                try:p=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,bufsize=1,env=env)
                finally:log.close()
                session={'process':p,'generation':object(),'action':request['action']};self.sessions[udid]=session
                threading.Thread(target=self._read,args=(udid,session),daemon=True).start()
            session['action']=request['action'];session['deadline']=time.monotonic()+55
            self._state(udid,'busy','正在恢复真实定位…' if request['action']=='clear' else '正在连接定位服务并设置坐标…')
            try:session['process'].stdin.write(json.dumps(request)+'\n');session['process'].stdin.flush()
            except (OSError,ValueError) as exc:
                self._state(udid,'unknown','定位请求发送失败，手机位置未确认。');raise RuntimeError('定位连接已断开，请重新连接后恢复真实定位。') from exc
            threading.Thread(target=self._timeout,args=(udid,session,session['deadline']),daemon=True).start()
    def _timeout(self,udid,session,deadline):
        delay=max(0,deadline-time.monotonic());time.sleep(delay)
        with self.lock:
            if self.sessions.get(udid) is not session or session.get('deadline')!=deadline or self.states.get(udid,{}).get('phase')!='busy':return
            self._state(udid,'unknown','定位请求超时，手机位置未确认；请重新连接后恢复真实定位。')
            session['process'].terminate()
    def _read(self,udid,session):
        p=session['process']
        try:
            while True:
                line=p.stdout.readline(8193)
                if not line:break
                if len(line)>8192:raise RuntimeError('定位后端返回无效数据。')
                value=json.loads(line)
                with self.lock:
                    if self.sessions.get(udid) is not session:return
                    event=value.get('event')
                    if event in ('applied','clear_sent','connected') and value.get('udid','').replace('-','').lower()!=udid.replace('-','').lower():raise RuntimeError('定位后端设备不匹配。')
                    if event=='applied':
                        lat,lon=coordinates(value['latitude'],value['longitude'])
                        self._state(udid,'applied',f'定位服务已接受坐标（{lat:.6f}, {lon:.6f}），请在手机地图中确认。',latitude=lat,longitude=lon,last_apply_at=time.time())
                    elif event=='clear_sent':self._state(udid,'clear_sent','已发送恢复真实定位指令，请在手机地图中确认。')
                    elif event=='connected':
                        if self.states.get(udid,{}).get('phase')=='applied':self.states[udid]['checked_at']=time.time()
                    elif event in ('error','restore_unknown'):
                        detail=str(value.get('error','连接中断'))[:1200]
                        self._state(udid,'unknown','定位未确认：'+detail+'\n请检查手机解锁、开发者模式及 Xcode 设备准备状态。')
            p.wait(timeout=3)
        except Exception as exc:
            with self.lock:
                if self.sessions.get(udid) is session:self._state(udid,'unknown','定位连接异常，手机位置未确认：'+str(exc)[:300])
            if p.poll() is None:p.terminate()
        finally:
            if p.poll() is None:
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            if p.stdout:p.stdout.close()
            if p.stdin:p.stdin.close()
            with self.lock:
                if self.sessions.get(udid) is session:
                    if self.states.get(udid,{}).get('phase') not in ('clear_sent','unknown'):
                        self._state(udid,'unknown','定位连接已结束，手机位置未确认；可重新连接后恢复真实定位。')
                    self.sessions.pop(udid,None)
    def shutdown(self,timeout=12):
        with self.lock:
            if self.closing:return
            self.guard.stop_all()
            self.closing=True;sessions=list(self.sessions.items())
            for udid,session in sessions:
                p=session['process']
                try:p.stdin.write(json.dumps({'action':'clear','udid':udid})+'\n');p.stdin.flush()
                except (OSError,ValueError):pass
        end=time.monotonic()+timeout
        for udid,session in sessions:
            p=session['process']
            try:p.wait(timeout=max(.1,end-time.monotonic()))
            except subprocess.TimeoutExpired:
                with self.lock:self._state(udid,'unknown','退出时未确认恢复真实定位，请重新连接后恢复或重启手机。')
                p.terminate()
                try:p.wait(timeout=2)
                except subprocess.TimeoutExpired:p.kill();p.wait()

        self.guard.wait_stopped()
