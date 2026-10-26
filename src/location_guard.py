"""Opt-in position correction driven only by fresh, identity-checked CoreLocation samples."""
import threading,time
from collections import deque
from location_readback import LocationReader,distance_m,StaleLocation

INTERVAL=10
class LocationGuard:
    def __init__(self,manager,reader_factory=LocationReader):self.manager=manager;self.reader_factory=reader_factory;self.sessions={};self.reader_locks={};self.release_threads=[]
    def _update(self,udid,**extra):
        old=self.manager.states.get(udid,{})
        self.manager._state(udid,old.get('phase','idle'),old.get('message','尚未设置模拟定位。'),**extra)
    def start(self,udid,target,background=True):
        # All transitions use the manager lock so a late read cannot outlive Clear/new target.
        with self.manager.lock:
            self.stop(udid)
            session={'target':target,'stop':threading.Event(),'reader':self.reader_factory(),'corrections':deque(),'blocked':False}
            self.sessions[udid]=session;self.reader_locks.setdefault(udid,threading.Lock())
            self._update(udid,guard_enabled=True,guard_status='waiting',guard_message='自动保持已开启；等待有效位置读回，每 10 秒检查一次。',correction_count=0)
            if background:threading.Thread(target=self._run,args=(udid,session),daemon=True).start()
            return session
    def stop(self,udid):
        with self.manager.lock:
            session=self.sessions.pop(udid,None)
            if session:
                session['stop'].set()
                self._update(udid,guard_enabled=False,guard_status='off',guard_message='自动保持已停止。')
                def release():
                    with self.reader_locks[udid]:
                        with self.manager.lock:
                            if udid in self.sessions:return
                        session['reader'].stop(udid)
                thread=threading.Thread(target=release,daemon=True);self.release_threads=[t for t in self.release_threads if t.is_alive()];self.release_threads.append(thread);thread.start()
    def wait_stopped(self,timeout=5):
        deadline=time.monotonic()+timeout
        for thread in list(self.release_threads):thread.join(max(0,deadline-time.monotonic()))
    def stop_all(self):
        with self.manager.lock:
            for udid in list(self.sessions):self.stop(udid)
    def _current(self,udid,session):return self.sessions.get(udid) is session and not session['stop'].is_set() and not self.manager.closing
    def _run(self,udid,session):
        def notify(message):
            with self.manager.lock:
                if self._current(udid,session):self._update(udid,guard_status='waiting',guard_message=message)
        try:
            with self.reader_locks[udid]:session['reader'].prepare(udid,session['stop'],notify)
        except Exception as exc:
            with self.manager.lock:
                if self._current(udid,session):self._update(udid,guard_status='unconfirmed',guard_message='控制连接未就绪，未自动纠偏：'+str(exc)[:400])
        while not session['stop'].wait(INTERVAL):
            with self.manager.lock:
                if not self._current(udid,session):return
            self.tick(udid,session)
    def tick(self,udid,session):
        with self.manager.lock:
            if not self._current(udid,session) or session['blocked']:return
            if self.manager.states.get(udid,{}).get('phase')=='busy':return
        try:
            with self.reader_locks[udid]:
                with self.manager.lock:
                    if not self._current(udid,session):return
                observed=session['reader'].read(udid)
            with self.manager.lock:
                if not self._current(udid,session):return
                state=self.manager.states.get(udid,{})
                if state.get('phase')=='busy':return
                if observed['timestamp']<state.get('last_apply_at',0)-1:
                    self._update(udid,guard_status='waiting',guard_message='已发送定位请求；等待请求之后的新位置样本，尚未确认生效。');return
                offset=distance_m(session['target'],(observed['latitude'],observed['longitude']))
                threshold=max(100,2*observed['accuracy'])
                details={'observed_latitude':observed['latitude'],'observed_longitude':observed['longitude'],'observed_at':observed['timestamp'],'observed_accuracy':observed['accuracy'],'distance_m':offset,'guard_checked_at':time.time()}
                if offset<=threshold:
                    self._update(udid,guard_status='confirmed',guard_message=f'本次读回在目标范围内：偏差 {offset:.0f} 米，定位精度 ±{observed["accuracy"]:.0f} 米。',**details);return
                now=time.monotonic();budget=session['corrections']
                while budget and now-budget[0]>300:budget.popleft()
                if len(budget)>=3:
                    session['blocked']=True
                    self._update(udid,guard_status='paused',guard_message='5 分钟内已纠偏 3 次，仍检测到偏离；已暂停自动修改，请检查其他定位工具或连接后重新设置。',**details);return
                budget.append(now)
                # Freshness/identity were validated by the reader; lock prevents a concurrent
                # restore or target change from slipping between this check and this write.
                self.manager._request(udid,{'action':'set','udid':udid,'latitude':session['target'][0],'longitude':session['target'][1]})
                count=state.get('correction_count',0)+1
                self._update(udid,guard_status='correcting',guard_message=f'检测到偏离 {offset:.0f} 米，正在恢复所选位置；随后读回确认。',correction_count=count,**details)
        except Exception as exc:
            with self.manager.lock:
                if self._current(udid,session):
                    self._update(udid,guard_status='waiting' if isinstance(exc,StaleLocation) else 'unconfirmed',guard_message=str(exc)[:500] if isinstance(exc,StaleLocation) else '位置未确认，未自动修改：'+str(exc)[:500])
