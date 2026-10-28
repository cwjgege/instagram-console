#!/usr/bin/env python3
"""Bounded manual Instagram sessions on one explicitly configured physical iPhone."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import signal
import subprocess
import threading
import time
import queue
import re
from setup_errors import SetupError,classify_setup_error,log_positions,attempt_text
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
import ai_comments
import diagnostics
from page_state import (PageBlocked, classify, visible_nodes, screen_height, search_input_node, search_layout, search_back_name, profile_back_name, collection_sheet_nodes, collection_sheet_close, deferred_apple_account_alert)
from app_paths import DATA as ROOT, SERVER, FROZEN, load_config, lock_device
from session_context import current as session_context
from watch_policy import playback_timing,playback_progress,playback_finished,observed_progress,watch_plan,replay_probability
from watch_relevance import watch_related as caption_watch_related

ELEMENT = 'element-6066-11e4-a52e-4f735466cecf'
RECONNECT_QUICK_DELAYS=(0,2,5)
RECONNECT_BACKOFF_DELAYS=(15,30,60)
RECONNECT_WINDOW_SECONDS=300
RECONNECT_WINDOW_LIMIT=9
SEARCH_STAGE_SECONDS={
    'open_explore':60,'locate_input':45,'focus_input':45,'submit_query':45,
    'verify_results':60,'select_result':45,'open_video':45,'return_results':45,
    'scroll_results':45,'return_reels':60,
}
SEARCH_STAGE_LABELS={
    'open_explore':'打开发现页','locate_input':'查找搜索框','focus_input':'确认搜索框',
    'submit_query':'提交搜索词','verify_results':'等待搜索结果','select_result':'选择结果视频',
    'open_video':'打开结果视频','watch_result':'观看结果视频','return_results':'返回结果列表',
    'scroll_results':'滚动结果列表','return_reels':'返回视频流',
}
def search_budget_seconds(cfg):
    count=int(cfg['search_videos_per_query']);scrolls=int(cfg['search_result_scrolls'])
    return min(900,180+count*(60+cfg['search_watch_max'])+max(0,count-1)*scrolls*30)

class ManualResume(BaseException):
    """Unwind all stale navigation/interaction frames after a human handoff."""
AUTOMATION_DEFAULTS = dict(like_interval_min=3,like_interval_max=8,recovery_enabled=True,recovery_attempts=3,recovery_run_limit=5,
    gesture_left=30,gesture_right=58,gesture_top=28,gesture_bottom=66,
    gesture_distance_min=22,gesture_distance_max=32,gesture_duration_min=220,gesture_duration_max=480,
    reconnect_enabled=True,reconnect_limit=2,close_business_chat=True,usb_wait_seconds=60,
    search_videos_per_query=2,search_result_scrolls=1,search_watch_min=10,search_watch_max=20,diagnostic_screenshots=False)

def validate_motion(cfg):
    if not 1<=cfg.get('like_interval_min',3)<=cfg.get('like_interval_max',8)<=100:
        raise ValueError('点赞间隔须为 1～100 条视频，且最小值不能大于最大值')
    for low,high,minimum,maximum in [('gesture_left','gesture_right',20,65),
        ('gesture_top','gesture_bottom',22,70),('gesture_distance_min','gesture_distance_max',15,40),
        ('gesture_duration_min','gesture_duration_max',180,800)]:
        if not minimum<=cfg[low]<=cfg[high]<=maximum:
            raise ValueError(f'{low} / {high} 须按从小到大填写，范围 {minimum}～{maximum}')
    if cfg['gesture_distance_max']>=cfg['gesture_bottom']-cfg['gesture_top']:
        raise ValueError('最大滑动距离必须小于安全区高度')
    if not 1<=cfg['reconnect_limit']<=3:raise ValueError('延后重试次数须为 1～3，另有 3 次快速重试')
    if not 15<=cfg.get('usb_wait_seconds',60)<=180:raise ValueError('USB 重新接入等待须为 15～180 秒')
    if not (1<=cfg.get('search_videos_per_query',2)<=5 and 1<=cfg.get('search_result_scrolls',1)<=3
        and 5<=cfg.get('search_watch_min',10)<=cfg.get('search_watch_max',20)<=90):
        raise ValueError('搜索视频数须为 1～5，滚动次数为 1～3，观看时长为 5～90 秒且从小到大')

class TransportLost(RuntimeError):
    pass

class InteractionSkipped(RuntimeError):
    """Abandon one like/save attempt without replaying an uncertain write."""

class ControlHTTPError(RuntimeError):
    def __init__(self,message,status,kind='',empty=False):
        super().__init__(message);self.status=status;self.kind=kind;self.empty=empty

def transport_error(exc):
    if isinstance(exc,ControlHTTPError):
        if exc.status in (502,503,504) or (exc.status>=500 and exc.empty) or exc.kind=='invalid session id':return True
    text=str(exc).casefold()
    return isinstance(exc,(TimeoutError,ConnectionError)) or any(s in text for s in (
        'socket hang up','connection refused','connection reset','timed out','invalid session id',
        'could not proxy command','remote end closed','econnreset','econnrefused',
        'unknown device or simulator udid','no route to host','network is unreachable'))

class Halt(Exception):
    pass

class NavigationLost(RuntimeError):
    """Recoverable navigation loss; never used for an unconfirmed write result."""
    pass

class WatchBudgetExpired(RuntimeError):
    pass

class SearchTimedOut(NavigationLost):
    pass

def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def call(method, path, data=None, timeout=35, server=None):
    req = Request((server or SERVER) + path, None if data is None else json.dumps(data).encode(),
                  {'Content-Type': 'application/json'}, method=method)
    try:
        with urlopen(req, timeout=timeout) as r:
            value = json.load(r)['value']
    except HTTPError as e:
        raw=e.read().decode(errors='replace');kind='';message=''
        try:
            parsed=json.loads(raw).get('value',{})
            if isinstance(parsed,dict):kind=str(parsed.get('error') or '');message=str(parsed.get('message') or '')
        except (ValueError,AttributeError,TypeError):message=raw
        if not raw.strip() and e.code==404 and path.startswith('/session/'):kind='invalid session id'
        detail=message.strip() or kind or str(e.reason) or '控制服务未返回错误说明'
        raise ControlHTTPError(f'HTTP {e.code}: '+detail[:1200],e.code,kind,not raw.strip()) from e
    if isinstance(value, dict) and value.get('error'):
        raise RuntimeError(str(value))
    return value


def toggle_is_active(button,kind):
    if button is None:return False
    label=button.get('label','').strip().casefold()
    old={'likes':{'unlike','unlike post'},'saves':{'remove from saved','unsave','unsave post'}}[kind]
    selected=button.get('value')=='1' or 'Selected' in button.get('traits','').split(', ')
    return label in old or (selected and label=={'likes':'liked','saves':'saved'}[kind])

class Runner:
    def __init__(self, config, stop=None, notify=None, seconds=None, browse_only=False, model_api_key=None, analysis_only=False):
        self.cfg = {**AUTOMATION_DEFAULTS,**ai_comments.DEFAULTS,**config}
        validate_motion(self.cfg)
        config = self.cfg
        self.model_api_key=ai_comments.api_key(model_api_key,config['ai_provider'])
        self.analysis_only=analysis_only
        self.managed=FROZEN or bool(config.get('managed_device'))
        self.server_url=config.get('server_url')
        self.ai_cache={}
        self.ai_missing_warned=False
        self.stop = stop or threading.Event()
        self.notify = notify or print
        self.seconds = seconds if seconds is not None else float(config['minutes']) * 60
        self.browse_only = browse_only
        self.runtime = Path(config['runtime_dir'])
        self.rng = random.SystemRandom()
        self.like_seen=set();self.like_next=self.rng.randint(config['like_interval_min'],config['like_interval_max'])
        self.path = None
        self.deadline = None
        self.swipes = self.searches = self.back_depth = self.position = 0
        self.counts = dict(likes=0, saves=0, comments=0, watched=0)
        self.day = datetime.now(ZoneInfo(config['timezone'])).strftime('%Y-%m-%d')
        self.run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.log_dir = self.runtime / 'runs' / self.run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.runtime / 'interaction-ledger.json'
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {}
        self.ledger.setdefault(self.day, {'likes':0,'saves':0,'comments':0,'posts':{},'comment_texts':[]})
        self.daily = self.ledger[self.day]
        self.daily.setdefault('ai_calls',0)
        self.stagnant = 0
        self.recoveries = 0
        self.recovery_times = []
        self.unchanged_swipes = 0
        self.search_videos = 0
        self.search_attempts = 0
        self.navigation_epoch = 0
        self.reconnections = 0
        self.reconnect_times = []
        self.in_interaction = False
        self.interaction_deadline = None
        self.search_deadline = None
        self.search_overall_deadline = None
        self.deferred_account_reminder = False
        self.video_watch = None
        self.refresh_required = False
        self.last_tree = None
        self.page_state = None
        self.active_search_term = None
        self.feature_state = {'search': {'enabled': bool(config.get('enable_search')), 'reason': ''}}
        self.search_failures = 0
        self.search_consecutive_failures = 0
        self.search_retry_at = None
        self.search_step = None
        self.pause_requested=threading.Event()
        self.pause_state='running'
        self.pause_enabled=False
        self._handling_pause=False
        self.handoff_skip_keys=set()
        self.skip_interactions_once=False
        self.current_post_key=None

    def request_pause(self):
        if self.pause_state=='running':
            self.pause_state='pausing';self.pause_requested.set()

    def request_resume(self):
        if self.pause_state=='paused':
            self.pause_state='resuming';self.pause_requested.clear()

    def pause_keepalive(self):
        try:
            self.http('GET',self.path+'/timeouts',timeout=3)
            return True
        except Exception:return False

    def manual_handoff(self):
        self.search_deadline=None;self.search_overall_deadline=None
        self._handling_pause=True
        started=time.monotonic()
        if self.current_post_key:self.handoff_skip_keys.add(self.current_post_key)
        reason=''
        reconnect_on_resume=False
        try:
            while True:
                self.pause_state='paused'
                self.log('paused',message=reason or '已暂停托管，可以手动操作手机；完成后点击继续托管。')
                heartbeat=time.monotonic()+30
                while self.pause_requested.is_set():
                    if self.stop.is_set() or (self.runtime/'STOP').exists():raise Halt('已手动停止')
                    if time.monotonic()>=heartbeat:
                        # Query Appium session settings only; never send a phone gesture or activate an app.
                        reconnect_on_resume=not self.pause_keepalive() or reconnect_on_resume
                        heartbeat=time.monotonic()+30
                    self.stop.wait(.1)
                self.log('resuming',message='正在重新识别手机页面，不会继续之前的互动…')
                try:
                    if reconnect_on_resume:self.connect();reconnect_on_resume=False
                    app=self.mobile('activeAppInfo')
                    if app.get('bundleId')!='com.burbn.instagram':
                        self.mobile('activateApp',bundleId='com.burbn.instagram');self.wait(1)
                    tree=self.snapshot();state=self.observe(tree)
                    if state.kind in ('input_unknown','sheet'):raise NavigationLost('请先关闭手动输入或浮层，再点击继续托管。')
                    self.reels(initial_tree=tree if state.kind!='other_app' else None)
                    post=self.read_reel()
                    if post['key']:self.handoff_skip_keys.add(post['key'])
                    break
                except Exception as exc:
                    if transport_error(exc) or getattr(exc,'kind','')=='invalid session id':reconnect_on_resume=True
                    self.pause_requested.set()
                    reason='继续托管未通过页面检查，保持暂停：'+str(exc)[:240]
                    self.log('resume_blocked',message=reason)
            self.navigation_epoch+=1;self.ai_cache.clear();self.active_search_term=None
            self.last_tree=None;self.page_state=None;self.stagnant=0;self.skip_interactions_once=True
            self.search_step=None;self.refresh_required=False;self.unchanged_swipes=0
            self.pause_state='running'
            self.log('resumed',message='已重新识别页面，继续浏览；跳过接管前及恢复时视频的互动。')
        finally:
            if self.deadline:self.deadline+=time.monotonic()-started
            self._handling_pause=False
        raise ManualResume()

    def http(self,method,path,data=None,timeout=35):
        url=getattr(self,'server_url',None)
        if url:return call(method,path,data,timeout,server=url)
        return call(method,path,timeout=timeout) if data is None else call(method,path,data,timeout=timeout)

    def log(self, event, **details):
        row = {'time':datetime.now().isoformat(timespec='seconds'),'event':event,**details}
        with (self.log_dir / 'events.jsonl').open('a') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        self.notify(row)

    def check(self):
        if self.stop.is_set() or (self.runtime / 'STOP').exists():
            raise Halt('已手动停止')
        if getattr(self,'pause_requested',None) is not None and self.pause_requested.is_set() and self.deadline and self.pause_enabled and not self.in_interaction and not self._handling_pause:
            self.manual_handoff()
        if self.deadline and not self._handling_pause and time.monotonic() >= self.deadline:
            raise Halt('本次时长已到')
        if getattr(self,'search_overall_deadline',None) is not None and time.monotonic()>=self.search_overall_deadline:
            raise SearchTimedOut('本次搜索达到整体时间上限，已暂停搜索并返回浏览')
        if getattr(self,'search_deadline',None) is not None and time.monotonic()>=self.search_deadline:
            stage=SEARCH_STAGE_LABELS.get(self.search_step,self.search_step or '未知阶段')
            raise SearchTimedOut('搜索阶段超时（'+stage+'），已暂停搜索并返回浏览')

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            self.stop.wait(min(.25, max(0, end-time.monotonic())))

    def command(self, method, suffix, data=None):
        self.check()
        readonly=method=='GET' or suffix=='/elements' or (
            suffix=='/execute/sync' and (data or {}).get('script')=='mobile: activeAppInfo')
        def send():
            deadlines=[d for d in (getattr(self,'interaction_deadline',None),getattr(self,'search_deadline',None),getattr(self,'search_overall_deadline',None),getattr(self,'watch_read_deadline',None) if readonly else None) if d is not None]
            deadline=min(deadlines) if deadlines else None
            if deadline is None:return self.http(method,self.path+suffix,data)
            remaining=deadline-time.monotonic()
            if remaining<=0:
                if getattr(self,'watch_read_deadline',None)==deadline:raise WatchBudgetExpired('本条观看预算已用完')
                if deadline in (getattr(self,'search_deadline',None),getattr(self,'search_overall_deadline',None)):raise SearchTimedOut('搜索请求超过剩余阶段或整体预算，暂停本轮搜索')
                raise InteractionSkipped('收藏确认已超时，跳过本条，不重复发送收藏操作')
            try:return self.http(method,self.path+suffix,data,timeout=min(35,remaining))
            except Exception as exc:
                if getattr(self,'watch_read_deadline',None)==deadline and transport_error(exc) and time.monotonic()>=deadline:
                    raise WatchBudgetExpired('播放进度读取未在本条预算内完成') from exc
                if transport_error(exc):self.check()
                raise
        try:
            return send()
        except Exception as exc:
            if suffix=='/actions' and transport_error(exc):
                raise TransportLost('滑动响应超时或异常，动作结果待确认，不重复发送') from exc
            if not readonly or not transport_error(exc):raise
            # Only reads can be retried. Never replay a click, input, or gesture.
            self.log('read_retry',message='页面读取超时或连接异常，短暂等待后重试')
            self.wait(.8)
            try:return send()
            except Exception as retry:
                if transport_error(retry):raise TransportLost('页面读取未获得响应，连接状态待确认') from retry
                raise

    def mobile(self, name, **args):
        try:return self.command('POST','/execute/sync',{'script':'mobile: '+name,'args':[args]})
        except Exception as exc:
            if name=='activateApp' and transport_error(exc):
                raise TransportLost('启动 Instagram 时控制连接失效，需要重新连接') from exc
            raise

    def find(self, name, optional=False):
        found = self.command('POST','/elements',{'using':'accessibility id','value':name})
        for entry in found:
            eid=entry.get(ELEMENT) or entry.get('ELEMENT')
            if self.command('GET','/element/'+eid+'/displayed'):
                return eid
        if optional:
            return None
        raise RuntimeError('未找到页面控件：' + name)

    def click(self, name):
        eid = self.find(name)
        try:self.command('POST','/element/'+eid+'/click',{})
        except Exception as exc:
            if name in ('reels-tab','BackButton','explore-tab','back-button','Back','Cancel','search-bar-back-button','profile-back-button') and transport_error(exc):
                raise TransportLost('导航时控制连接中断，需重新识别页面') from exc
            raise

    def observe(self, tree):
        self.last_tree=tree
        queries=tuple(self.cfg['keywords'])+((self.active_search_term,) if self.active_search_term else ())
        state=classify(tree,queries,self.active_search_term)
        if self.page_state is None or self.page_state.kind!=state.kind:
            self.log('page_state',message=state.reason,state=state.kind,
                capabilities={'search_input':state.search_input,'video_controls':state.video_controls})
        self.page_state=state
        if state.blocked:raise PageBlocked(state.reason)
        return state

    def snapshot(self):
        # App transitions can briefly return an empty/system bundle. Only retry reads:
        # never repeat a gesture or steal foreground. Only one exactly recognized system reminder may be deferred.
        for attempt in range(3):
            self.check()
            info=self.mobile('activeAppInfo')
            if isinstance(info,dict) and info.get('bundleId') in ('com.burbn.instagram','com.apple.springboard'):
                tree=ET.fromstring(self.command('GET','/source'))
                if deferred_apple_account_alert(tree) is not None:
                    if self.deferred_account_reminder:raise PageBlocked('Apple 账号验证提示再次出现，请手动检查系统账号；本轮不再自动关闭。')
                    found=self.command('POST','/elements',{'using':'-ios predicate string','value':"type == 'XCUIElementTypeButton' AND visible == true AND enabled == true AND label == 'Not Now'"})
                    if len(found)!=1:raise PageBlocked('Apple 账号提示的稍后处理按钮不唯一，请手动处理。')
                    self.deferred_account_reminder=True
                    self.command('POST','/element/'+(found[0].get(ELEMENT) or found[0].get('ELEMENT'))+'/click',{})
                    self.log('system_reminder_deferred',message='已暂时关闭 Apple 账号验证提醒（Not Now），未输入密码或打开设置。')
                    self.wait(.5)
                    continue
                app=tree if tree.tag=='XCUIElementTypeApplication' else tree.find('.//XCUIElementTypeApplication')
                if app is None or app.get('bundleId') in (None,'','com.burbn.instagram'):
                    self.observe(tree)
                    if attempt:self.log('foreground_recovered',message='只读复查已确认 Instagram 前台，无需重启或恢复页面',checks=attempt+1)
                    return tree
            self.last_tree=None
            if attempt<2:
                self.log('foreground_recheck',message='前台状态暂未确认，正在只读复查',attempt=attempt+1)
                self.wait(.35 if attempt==0 else .7)
        raise NavigationLost('连续三次检查未确认 Instagram 前台，需重新识别页面')

    def diagnose(self, stage, exc):
        # Diagnostics must never mask the original error or send a UI action.
        try:
            screenshot=None
            if self.cfg.get('diagnostic_screenshots') and self.path and not self.stop.is_set():
                try:screenshot=self.http('GET',self.path+'/screenshot',timeout=5)
                except Exception:pass
            folder=diagnostics.record(self.log_dir,stage+(':'+self.search_step if stage=='search' and self.search_step else ''),exc,self.last_tree,self.page_state,
                self.feature_state,self.counts,screenshot)
            if folder:self.log('diagnostic_saved',message='已记录脱敏页面诊断，可在运行记录中导出')
        except Exception:pass

    def wait_for_usb(self):
        from device_manager import scan_devices,usb_serials,validate_device
        end=time.monotonic()+self.cfg['usb_wait_seconds']
        announced=False
        while True:
            self.check()
            physical=usb_serials()
            if self.cfg['device_udid'].replace('-','').lower() in physical:
                devices=scan_devices()
                device=next((d for d in devices if d['udid']==self.cfg['device_udid']),None)
                if device:
                    validate_device(device);self.device_info=device
                    if announced:self.log('device_ready',message='指定 iPhone 已重新通过 USB 接入，电脑配对和开发者模式已检查')
                    return
            if not announced:
                self.log('device_wait',message='未检测到指定 iPhone 的 USB 连接，等待重新接入；请检查线缆并解锁手机',seconds=self.cfg['usb_wait_seconds'])
                announced=True
            if time.monotonic()>=end:
                raise RuntimeError('等待 USB 重新接入超时。请重新连接、解锁这台 iPhone 后再开始；不会切换到其他手机或网络连接')
            self.wait(min(3,max(0,end-time.monotonic())))

    def connect(self, force_new=False, quick=False):
        self.log('connecting', message='正在连接指定 iPhone')
        import connections
        connection=connections.load(self.cfg['device_udid']) if self.managed else {'mode':'usb'}
        network=connection['mode']!='usb'
        if not network:
            if quick:
                from device_manager import usb_serials
                if self.cfg['device_udid'].replace('-','').lower() not in usb_serials():raise TransportLost('指定 USB 手机尚未接入')
            self.wait_for_usb()
        if self.managed:
            from device_manager import child_env,prepare_vendor,signing_record
            from runtime_manager import ensure_server,ensure_legacy
            notify=lambda message:self.log('connecting',message=message)
            self.server_url=ensure_server(self.cfg['device_udid'],notify,self.stop)['url']
            self.device_env=child_env(prepare_vendor())
            record=signing_record(self.cfg['device_udid'])
            if record and hasattr(self,'device_info'):
                legacy=str(self.device_info['ios']).split('.')[0]=='16'
                if legacy!=(record.get('controller')=='legacy16'):
                    raise SetupError('controller_mismatch','手机系统与已配置的控制组件不匹配，请在此设备向导安装对应的 iOS 16 / 新版组件。','设备支持')
            if network:
                self.network_url=(connections.quick_wifi if quick else connections.ensure_wifi)(self.cfg['device_udid'],notify,self.stop)
                force_new=True
            elif record and record.get('controller')=='legacy16':ensure_legacy(self.cfg['device_udid'],record,notify,self.stop)
        try:
            self.http('GET','/status',timeout=3)
        except Exception:
            if self.managed:raise RuntimeError('内置控制服务未能连接，请打开设备配置检查。')
            env = os.environ.copy()
            env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:' + env.get('PATH','')
            self.server_log = (self.runtime / 'server-console.log').open('a')
            subprocess.Popen(['/bin/zsh',str(self.runtime/'start-server.sh')],env=env,
                             stdout=self.server_log,stderr=subprocess.STDOUT,start_new_session=True)
            for _ in range(30):
                self.wait(1)
                try:
                    self.http('GET','/status',timeout=2)
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError('Appium 服务未能启动')
        state = self.runtime / 'active-session.json'
        if state.exists():
            try:cached_context=json.loads(state.read_text())
            except (OSError,ValueError):cached_context={}
            if not isinstance(cached_context,dict):cached_context={}
            if cached_context.get('_igconsole_context')!=session_context():
                caps=cached_context.get('capabilities',{})
                if caps.get('udid',caps.get('appium:udid')) not in (None,self.cfg['device_udid']):
                    raise RuntimeError('缓存设备标识不一致，未覆盖其他设备会话。')
                # Session IDs and local URLs copied from another Mac are not proof
                # of ownership. Forget only the cache; never DELETE that session ID.
                state.unlink(missing_ok=True);force_new=True
                self.log('session_context_reset',message='检测到版本或电脑变化，保留配置并重新建立控制会话；不重装组件。')
        if state.exists() and not force_new:
            try:old=json.loads(state.read_text())
            except (OSError,ValueError):old={};state.unlink(missing_ok=True)
            caps = old.get('capabilities',{})
            if old.get('network_url'):force_new=True
            if not force_new and caps.get('udid',caps.get('appium:udid')) == self.cfg['device_udid'] and (not self.managed or old.get('server_url')==self.server_url):
                try:
                    self.path = '/session/' + old['sessionId']
                    self.log('connecting',message='正在检查上次的手机控制连接…')
                    info=self.http('POST',self.path+'/execute/sync',{'script':'mobile: activeAppInfo','args':[{}]},timeout=15)
                    if not isinstance(info,dict) or not info.get('bundleId'):
                        raise RuntimeError('旧连接未返回当前应用信息')
                    self.check()
                    self.log('connected', message='已连接 iPhone')
                    return
                except Halt:
                    raise
                except Exception:
                    self.log('session_stale',message='上次连接已失效，清理后重新建立连接')
                    try:self.http('DELETE',self.path,timeout=8)
                    except Exception:pass
                    state.unlink(missing_ok=True)
                    self.path = None
                    force_new=True
        if force_new and state.exists():
            try:cached=json.loads(state.read_text())
            except (OSError,ValueError):cached={}
            caps=cached.get('capabilities',{})
            if cached and caps.get('udid',caps.get('appium:udid'))!=self.cfg['device_udid']:
                raise RuntimeError('缓存设备标识不一致，未删除其他设备会话，请检查此设备配置。')
            if cached and (not self.managed or cached.get('server_url')==self.server_url):
                sid=cached.get('sessionId','')
                if re.fullmatch('[A-Za-z0-9-]{1,80}',sid):
                    try:self.http('DELETE','/session/'+sid,timeout=8)
                    except Exception:pass
            state.unlink(missing_ok=True);self.path=None
        payload = connections.network_payload(self.cfg,self.network_url) if network else json.loads((self.runtime / 'session-request.json').read_text())
        # Keyboard snapshots on the tested device take 24–27s; keep the proxy
        # timeout below our 35s HTTP cap, with search phases still bounded.
        payload['capabilities']['alwaysMatch']['appium:wdaConnectionTimeout']=30000
        payload['capabilities']['alwaysMatch']['appium:waitForIdleTimeout']=1
        # Old saved requests must not reset on-device developer trust by uninstalling WDA.
        payload['capabilities']['alwaysMatch']['appium:useNewWDA']=False
        if force_new:
            if self.path:
                try:self.http('DELETE',self.path,timeout=8)
                except Exception:pass
            self.check()
        if payload['capabilities']['alwaysMatch']['appium:udid'] != self.cfg['device_udid']:
            raise RuntimeError('设备标识不一致，已停止')
        if self.managed and not network:
            from runtime_manager import endpoints
            ports=endpoints(self.cfg['device_udid']);caps=payload['capabilities']['alwaysMatch']
            caps.update({'appium:wdaLocalPort':ports['wda'],'appium:mjpegServerPort':ports['mjpeg'],'appium:wdaRemotePort':8100})
            if not network and record and record.get('controller')=='legacy16':caps['appium:webDriverAgentUrl']=f"http://127.0.0.1:{ports['wda']}"
        self.log('connecting',message='正在建立手机控制会话，停止时需等待当前连接命令结束…')
        positions=log_positions(self.runtime)
        try:value=self.http('POST','/session',payload,timeout=45 if quick else 240)
        except Exception as exc:
            issue=classify_setup_error(exc,attempt_text(self.runtime,positions))
            if issue.code not in ('unknown','connection'):
                if self.managed:
                    import device_manager as dm
                    pending=bool(record and dm.stage_installed_candidate(getattr(self,'device_info',{'udid':self.cfg['device_udid'],'ios':self.cfg.get('device_ios','')}),record['team_id']))
                    atomic_json(self.runtime/'setup-state.json',{'status':'awaiting_trust' if pending and issue.code=='developer_untrusted' else 'awaiting_validation' if pending else 'failed','can_verify':pending,'code':issue.code,'stage':issue.stage,'message':str(issue)})
                raise issue from exc
            if transport_error(exc):raise TransportLost('建立手机控制会话时连接中断') from exc
            raise

        if self.managed:
            value['server_url']=self.server_url
            if network:value['network_url']=self.network_url
            if connection['mode']!='remote':
                atomic_json(self.runtime/'setup-state.json',{'status':'ready','message':'控制组件已连接验证。'})
                (self.runtime/'pending-setup.json').unlink(missing_ok=True)
        value['_igconsole_context']=session_context()
        atomic_json(state,value)
        self.path = '/session/' + value['sessionId']
        self.check()
        self.log('connected', message='已连接 iPhone')

    def control_available(self):
        """Probe the phone through the current session, without replaying UI writes.

        A local Appium /status response cannot establish phone reachability.
        This read has its own budget after the failed operation's budget expires.
        """
        names=('search_deadline','search_overall_deadline','interaction_deadline','watch_read_deadline')
        saved={name:getattr(self,name,None) for name in names}
        try:
            for name in names:setattr(self,name,None)
            return self._probe_control_available()
        finally:
            for name,value in saved.items():setattr(self,name,value)

    def _probe_control_available(self):
        # Use different read paths: activeAppInfo can stall while gestures and
        # the window endpoint still work. Local Appium health is not evidence.
        probes=(('GET','/window/rect',None),('POST','/execute/sync',{'script':'mobile: activeAppInfo','args':[{}]}))
        for attempt,(method,suffix,data) in enumerate(probes):
            self.check()
            try:
                info=self.http(method,self.path+suffix,data,timeout=15)
                valid=isinstance(info,dict) and ((suffix=='/window/rect' and
                    all(isinstance(info.get(k),(int,float)) and info[k]>0 for k in ('width','height'))) or
                    (suffix=='/execute/sync' and isinstance(info.get('bundleId'),str) and bool(info['bundleId'])))
                if valid:
                    self.log('control_probe_ok',message='手机控制通道仍可响应，保留当前连接',attempt=attempt+1)
                    return True
            except (Halt,PageBlocked):raise
            except Exception as exc:
                self.log('control_probe_failed',message='当前会话尚未确认手机响应',attempt=attempt+1,
                         reason=diagnostics.redact(str(exc)))
            if attempt==0:self.wait(1)
        return False

    def recover_connection(self):
        self.check()
        if self.in_interaction:raise RuntimeError('互动过程中连接中断，结果未确认，已停止且不会重发')
        if not self.cfg['reconnect_enabled']:raise RuntimeError('控制连接中断，自动重连已关闭')
        delays=RECONNECT_QUICK_DELAYS+RECONNECT_BACKOFF_DELAYS[:self.cfg['reconnect_limit']]
        for index,delay in enumerate(delays):
            self.check()
            now=time.monotonic();self.reconnect_times=[t for t in self.reconnect_times if now-t<RECONNECT_WINDOW_SECONDS]
            if len(self.reconnect_times)>=RECONNECT_WINDOW_LIMIT:
                raise RuntimeError('控制连接恢复失败，5 分钟内重连次数已用完；请检查手机与连接后重新开始')
            quick=index<len(RECONNECT_QUICK_DELAYS);phase='快速重连' if quick else '延后重连'
            self.log('reconnect_wait',message=phase+'：等待 '+str(delay)+' 秒后尝试',attempt=index+1,total=len(delays),delay_seconds=delay,phase='quick' if quick else 'backoff')
            if delay:self.wait(delay)
            self.check();self.reconnections+=1;self.reconnect_times.append(time.monotonic())
            self.log('reconnecting',message='正在'+phase+'并检查视频页面',reconnections=self.reconnections,attempt=index+1,phase='quick' if quick else 'backoff')
            try:
                self.connect(force_new=True,quick=quick)
                self.recover_reels(prefer_navigation=True)
            except (Halt,PageBlocked):raise
            except Exception as exc:
                if isinstance(exc,TransportLost) or transport_error(exc) or isinstance(exc,NavigationLost):
                    self.log('reconnect_attempt_failed',message='本次重连或视频页核验未完成，按计划继续尝试',attempt=index+1,reason=diagnostics.redact(str(exc)))
                    continue
                raise
            self.skip_interactions_once=True;self.current_post_key=None
            self.log('reconnected',message='连接及视频页面已恢复，继续浏览；跳过恢复时视频的互动',reconnections=self.reconnections,attempt=index+1)
            return
        raise RuntimeError('控制连接恢复失败，3 次快速重试及延后重试次数已用完；请检查手机与连接后重新开始')

    def confirm_reel_loaded(self):
        for attempt in range(3):
            try:return self.read_reel()
            except NavigationLost:
                tree=self.last_tree
                nodes=visible_nodes(tree) if tree is not None else []
                names={e.get('name') for e in nodes}
                selected=any(e.get('name')=='reels-tab' and e.get('value')=='1' for e in nodes)
                loading=(self.page_state is not None and self.page_state.kind=='main' and selected
                    and {'reels-viewer','sundial-viewer-video-collection-view'}<=names)
                if not loading or attempt==2:raise
                self.log('reels_loading',message='已进入 Reels，正在等待视频控件加载；暂不重复导航',attempt=attempt+1)
                self.wait(.8)

    def reels(self, initial_tree=None):
        # A caller may pass the tree it just verified, with no intervening action.
        # Never retain this tree across navigation: each click invalidates it.
        started=time.monotonic()
        if initial_tree is None:
            self.mobile('activateApp',bundleId='com.burbn.instagram')
            self.wait(1)
        tree=initial_tree
        for _ in range(4):
            if tree is None:tree=self.snapshot()
            state=self.observe(tree)
            if state.kind in ('input_unknown','sheet'):raise NavigationLost(state.reason)
            if state.kind=='reels':
                self.confirm_reel_loaded()
                self.log('reels_returned',message='已确认返回短视频页',seconds=round(time.monotonic()-started,3))
                return
            names={e.get('name') for e in visible_nodes(tree)}
            if state.kind=='profile':
                target=profile_back_name(tree)
            elif 'reels-tab' in names:
                target='reels-tab'
            else:
                target=next((name for name in ('back-button','BackButton') if name in names),None)
                target=target or search_back_name(tree) or ('Cancel' if 'Cancel' in names else None)
            if not target:raise NavigationLost('未找到 Instagram 主页面入口')
            self.click(target)
            tree=None
            self.wait(.7)
            if target=='reels-tab':
                self.confirm_reel_loaded()
                self.log('reels_returned',message='已确认返回短视频页',seconds=round(time.monotonic()-started,3))
                return
        raise NavigationLost('返回 Instagram 主页面的尝试已用完')

    def read_reel(self):
        observed=time.monotonic()
        for attempt in range(3):
            tree = self.snapshot()
            state=self.observe(tree)
            nodes = [e for e in tree.iter() if e.get('visible')=='true']
            names = {e.get('name') for e in nodes}
            is_reels=any(e.get('name')=='reels-tab' and e.get('value')=='1' for e in nodes) or 'Reels' in names or ('reels-viewer' in names and 'sundial-viewer-video-collection-view' in names)
            modal='ig-partial-modal-sheet-view-controller-content' in names
            input_open=any(e.tag in ('XCUIElementTypeTextField','XCUIElementTypeTextView','XCUIElementTypeSecureTextField') for e in nodes)
            if state.kind=='reels' and is_reels and not modal and not input_open and 'like-button' in names and 'comment-button' in names:
                break
            if collection_sheet_nodes(tree):raise NavigationLost('出现收藏夹选择弹层，需关闭后跳过本次互动')
            if attempt<2:
                self.wait(.7)
        else:
            raise NavigationLost('当前不是可识别的 Reels 页面')
        author = next((e.get('label','')[8:] for e in nodes if e.get('label','').startswith('Post by ')), '')
        captions = []
        for e in nodes:
            if 'caption' in e.get('name','').lower():
                captions.extend(x.get('label','') for x in e.iter() if x.tag=='XCUIElementTypeStaticText')
        caption = '\n'.join(dict.fromkeys(captions)).strip()
        # An absent caption is not enough evidence to identify a video or interact.
        key = hashlib.sha256((author+'\n'+caption).encode()).hexdigest()[:24] if author and caption else None
        if key and getattr(self,'_visible_video_key',None)!=key:
            self._visible_video_key=key;self._visible_video_since=observed
        return {'author':author,'caption':caption,'key':key,'nodes':nodes,
                'observed_at':self._visible_video_since if key else observed,
                'timing_at':observed,'playback':playback_timing(nodes),'progress':playback_progress(nodes)}

    def watch_video(self,post,related):
        now=time.monotonic();key=post.get('key')
        current=getattr(self,'video_watch',None)
        if not key or not current or current['key']!=key:
            plan=watch_plan(post if key else {},related and bool(key),self.rng)
            anchor=post.get('observed_at',now)
            if not isinstance(anchor,(int,float)) or not 0<=now-anchor<=3600:anchor=now
            current={**plan,'key':key,'related':related,'deadline':anchor+plan['seconds'] if plan['seconds'] is not None else None,
                     'done':False,'waiting_restart':False}
            self.video_watch=current
        if current['done']:return
        if not current['complete_intent']:
            remaining=max(0,current['deadline']-now)
            self.log('watch_timing',message='相关内容，本次选择短看后跳过' if related else '不相关内容，随机短看后跳过',
                     timing_source='short',remaining_seconds=round(remaining,2),complete_probability=current['complete_probability'])
            self.wait(remaining);current['done']=True;return
        self.log('watch_timing',message='本次选择按进度看到结束附近，无固定秒数上限',timing_source=current['basis'],
                 related=related,duration=current['duration'],complete_probability=current['complete_probability'])
        def complete_cycle(progress,restarted):
            if current['pass_index']>=2 or self.rng.random()>=replay_probability(related):
                current['done']=True
                self.log('watch_complete',message='本次观看结束，准备翻页',progress=progress);return True
            replay=watch_plan({'progress':progress},related,self.rng,pass_index=2)
            current.update(replay)
            if not replay['complete_intent']:
                current['deadline']=time.monotonic()+replay['seconds']
                self.log('watch_replay_brief',message='已看过一遍，本次回看倾向提前退出',seconds=round(replay['seconds'],2))
                self.wait(replay['seconds']);current['done']=True;return True
            current['waiting_restart']=not restarted
            self.log('watch_replay_complete',message='本次回看选择继续看到结束附近')
            return False
        progress=observed_progress(post);unchanged_since=now;missing=0
        while True:
            if progress is None:
                missing+=1
                if missing>=3:
                    current['done']=True
                    self.log('watch_progress_unavailable',message='多次未读到播放进度，停止等待本条视频');return
            elif not current['waiting_restart'] and playback_finished(None,progress):
                if complete_cycle(progress,False):return
            self.wait(1.5)
            self.watch_read_deadline=time.monotonic()+8
            try:fresh=self.read_reel()
            except Exception as exc:
                if not isinstance(exc,(WatchBudgetExpired,TransportLost)) and not transport_error(exc):raise
                self.watch_read_deadline=None
                self.log('watch_read_timeout',message='播放进度读取未完成，跳过进度判断，尝试一次翻页；不据此判定断线')
                current['done']=True
                self.log('watch_progress_skipped',message='跳过本条进度和互动，单次翻页后核对视频控件')
                return True
            finally:self.watch_read_deadline=None
            if fresh.get('key')!=key:raise InteractionSkipped('观看期间视频已变化，跳过旧视频互动')
            following=observed_progress(fresh);now=time.monotonic()
            if following is None:
                progress=None;continue
            missing=0
            if current['waiting_restart']:
                if following<95:current['waiting_restart']=False
            elif progress is not None and playback_finished(progress,following):
                if complete_cycle(following,following<97):return
            if following!=progress:unchanged_since=now
            elif now-unchanged_since>=30:
                current['done']=True
                self.log('watch_stalled',message='播放进度持续不变，停止等待并准备翻页',progress=following);return
            progress=following

    def recover_reels(self,prefer_navigation=False):
        """Navigate only with observed controls; abandon the previous post on recovery."""
        self.check()
        if not self.cfg.get('recovery_enabled',True):
            raise RuntimeError('页面异常，自动恢复已关闭')
        self.recovery_times=[t for t in self.recovery_times if time.monotonic()-t<300]
        if len(self.recovery_times)>=self.cfg.get('recovery_run_limit',5):
            raise RuntimeError('最近 5 分钟内页面恢复过于频繁，已停止，请检查手机页面或连接')
        if prefer_navigation:
            try:tree=self.snapshot();state=self.observe(tree)
            except NavigationLost:tree=state=None
            if state is not None and state.kind=='input_unknown':raise PageBlocked(state.reason)
            if state is not None and state.kind in ('search_input','search_context','search_results','explore','profile','main','reels'):
                self.reels(initial_tree=tree)
                post=self.read_reel()
                self.recoveries+=1;self.recovery_times.append(time.monotonic());self.navigation_epoch+=1
                self.position=self.back_depth=self.stagnant=self.unchanged_swipes=0;self.refresh_required=False
                self.log('recovered',message='重连后已通过已识别导航回到视频页',recoveries=self.recoveries)
                return post
        attempts=max(1,min(5,int(self.cfg.get('recovery_attempts',3))))
        previous_action=None
        self.log('recovering',message='页面识别异常，正在尝试恢复 Reels')
        for attempt in range(attempts):
            self.check()
            try:
                post=None if self.refresh_required and attempt==0 else self.read_reel()
            except NavigationLost:
                post=None
            if post is not None:
                self.recoveries+=1
                self.recovery_times.append(time.monotonic())
                self.navigation_epoch+=1
                self.position=self.back_depth=self.stagnant=self.unchanged_swipes=0
                self.refresh_required=False
                self.log('recovered',message='已恢复 Reels，重新识别当前视频',recoveries=self.recoveries)
                return post
            info=self.mobile('activeAppInfo')
            if info.get('bundleId')!='com.burbn.instagram':
                self.mobile('activateApp',bundleId='com.burbn.instagram')
                self.log('recovery_action',message='重新打开 Instagram',attempt=attempt+1)
                self.wait(1)
                previous_action='activate'
                continue
            tree=self.snapshot()
            state=self.observe(tree)  # System alerts are handled by snapshot and never auto-dismissed.
            nodes=[e for e in tree.iter() if e.get('visible')=='true']
            names={e.get('name') for e in nodes}
            fields=[e for e in nodes if e.tag in ['XCUIElementTypeTextField','XCUIElementTypeTextView','XCUIElementTypeSecureTextField','XCUIElementTypeSearchField']]
            # An observed ad end card includes a default inquiry, not a composed DM.
            # Close only this complete signature; modified input stays untouched.
            known_chat=(self.cfg['close_business_chat'] and len(fields)==1
                and fields[0].tag=='XCUIElementTypeTextField'
                and fields[0].get('value')=='Hello, can I get more info on this?'
                and any(e.get('label','').startswith('Business chat · ') for e in nodes)
                and 'Have a question?\nChat with us' in names
                and 'Send Message' in names
                and sum(e.get('name')=='ig icon x pano outline 24' for e in nodes)==1
                and not any(e.tag=='XCUIElementTypeKeyboard' for e in nodes)
                and any(e.get('name')=='reels-tab' and e.get('value')=='1' for e in nodes))
            search_back=search_back_name(tree)
            search_back=search_back or next((name for name in ('Cancel','BackButton') if name in names),None)
            if state.kind=='explore' and search_input_node(tree) is not None and 'reels-tab' in names:search_back='reels-tab'
            known_search=state.kind in ('search_input','search_context','search_results','explore') and bool(search_back)
            known_collection=collection_sheet_close(tree) is not None
            if (fields or state.kind=='input_unknown') and not known_chat and not known_search and not known_collection:
                raise RuntimeError('页面包含输入框，已停止恢复，保留当前输入内容')
            action=None
            dismiss=[e for e in nodes if e.tag=='XCUIElementTypeButton' and e.get('label','').casefold()=='dismiss']
            if collection_sheet_nodes(tree):
                try:closed=self.close_collection_sheet(tree)
                except NavigationLost:
                    self.wait(.4);continue
                if not closed:raise RuntimeError('收藏夹弹层没有唯一可确认的关闭入口，请手动关闭后继续。')
                action='close_collection'
            elif profile_back_name(tree):
                self.click(profile_back_name(tree));action='back'
            elif known_chat:
                self.click('ig icon x pano outline 24');action='close_business_chat'
            elif known_search:
                self.click(search_back);action='back'
            elif 'ig-partial-modal-sheet-view-controller-content' in names and len(dismiss)==1:
                found=self.command('POST','/elements',{'using':'-ios predicate string',
                    'value':"type == 'XCUIElementTypeButton' AND visible == true AND label == 'Dismiss'"})
                if len(found)==1:
                    eid=found[0].get(ELEMENT) or found[0].get('ELEMENT')
                    self.command('POST','/element/'+eid+'/click',{})
                    action='close_sheet'
            elif 'reels-tab' in names and previous_action!='reels':
                self.click('reels-tab');action='reels'
            elif 'back-button' in names and 'reels-viewer' in names:
                self.click('back-button');action='back'
            elif 'BackButton' in names:
                self.click('BackButton');action='back'
            elif 'reels-tab' in names:
                selected=any(e.get('name')=='reels-tab' and e.get('value')=='1' for e in nodes)
                if selected and previous_action!='swipe':
                    self.gesture('down');action='swipe'
                else:
                    self.click('reels-tab');action='reels'
            if action is None:
                raise RuntimeError('没有识别到可靠的恢复入口，请手动返回 Reels')
            self.log('recovery_action',message={'close_collection':'关闭收藏夹选择弹层，跳过本次收藏','close_sheet':'关闭已识别的浮层','reels':'重新进入 Reels',
                'back':'返回上一页','swipe':'在 Reels 内竖向滑动一次',
                'close_business_chat':'关闭已识别的广告咨询面板，未发送消息'}[action],attempt=attempt+1)
            previous_action=action
            self.wait(1)
        # Verify the last permitted action; do not spend another action on failure.
        try:
            post=self.read_reel()
        except NavigationLost:
            raise RuntimeError('页面恢复尝试已用完，请手动处理后重新开始') from None
        self.recoveries+=1
        self.recovery_times.append(time.monotonic())
        self.navigation_epoch+=1
        self.position=self.back_depth=self.stagnant=self.unchanged_swipes=0
        self.refresh_required=False
        self.log('recovered',message='已恢复 Reels，重新识别当前视频',recoveries=self.recoveries)
        return post

    def consider_like(self,post,related):
        if self.browse_only or not self.cfg['enable_likes'] or not post['key']:return
        if post['key'] in self.like_seen:return
        self.like_seen.add(post['key'])
        if len(self.like_seen)<self.like_next or not related:return
        if self.daily['likes']>=self.cfg['max_likes_per_day']:return
        if self.daily['posts'].get(post['key'],{}).get('likes'):return
        self.in_interaction=True
        self.toggle_on(post,'likes','like-button',{'like','like post'})
        self.in_interaction=False
        # Advance even when the visible button was already active or unavailable.
        # An uncertain write raises above and is never retried here.
        self.like_next=len(self.like_seen)+self.rng.randint(self.cfg['like_interval_min'],self.cfg['like_interval_max'])

    def watch_related(self,post):
        return caption_watch_related(post,self.cfg['keywords'],self.cfg['relevance_terms'],self.cfg['exclude_keywords'])

    def related(self, post):
        caption = post['caption'].casefold()
        return bool(post['key'] and any(t.casefold() in caption for t in self.cfg['relevance_terms'])
                    and not any(t.casefold() in caption for t in self.cfg['exclude_keywords']))

    def reserve(self, post, kind, text=None):
        entry = self.daily['posts'].setdefault(post['key'],{})
        if entry.get(kind) or self.daily[kind] >= self.cfg['max_'+kind+'_per_day']:
            return False
        # Reserve before sending: a network failure must never trigger a duplicate retry.
        entry[kind] = 'pending'
        self.daily[kind] += 1
        if text:
            self.daily['comment_texts'].append(text)
        atomic_json(self.ledger_path,self.ledger)
        return True

    def completed(self, post, kind):
        self.daily['posts'][post['key']][kind] = 'done'
        self.counts[kind] += 1
        atomic_json(self.ledger_path,self.ledger)
        self.log(kind, message={'likes':'已点赞','saves':'已收藏','comments':'已评论'}[kind], author=post['author'])

    def toggle_on(self, post, kind, name, allowed_labels):
        previous=getattr(self,'interaction_deadline',None)
        if kind=='saves':self.interaction_deadline=time.monotonic()+12
        try:
            return self._toggle_on(post,kind,name,allowed_labels)
        except (NavigationLost,TransportLost) as exc:
            raise InteractionSkipped('互动页面或连接变化，已放弃本条互动') from exc
        except Exception as exc:
            if transport_error(exc) or (isinstance(exc,ControlHTTPError) and exc.kind in ('stale element reference','no such element')):
                raise InteractionSkipped('互动控件或连接异常，结果未确认，不会重发') from exc
            raise
        finally:self.interaction_deadline=previous

    def _toggle_on(self, post, kind, name, allowed_labels):
        current = self.read_reel()
        if current['key'] != post['key']:
            raise InteractionSkipped('视频发生变化，已跳过本条互动')
        button = next((e for e in current['nodes'] if e.get('name')==name),None)
        if (button is None or toggle_is_active(button,kind) or button.get('value')=='1'
            or button.get('label','').casefold() not in allowed_labels):
            return  # Already active, translated UI, or unknown state: never toggle off.
        if not self.reserve(post,kind):
            return
        self.click(name)
        for delay in (1,.8,1.2):
            self.wait(delay)
            after=self.read_reel()
            if after['key']!=post['key']:break
            button=next((e for e in after['nodes'] if e.get('name')==name),None)
            if toggle_is_active(button,kind):
                self.completed(post,kind)
                return
        raise InteractionSkipped('互动结果无法确认，已跳过；不会重试或重复点击')

    def continue_after_interaction(self,post):
        """Do not dwell on the failed post again, or replay its uncertain social write."""
        self.in_interaction=False;self.skip_interactions_once=True
        try:
            try:current=self.read_reel()
            except NavigationLost:current=self.recover_reels()
        except TransportLost:
            self.recover_connection();current=self.read_reel()
        if current.get('key') and current['key']!=post.get('key'):
            self.skip_interactions_once=False
            return
        if not current.get('key'):return
        self.check()
        try:
            self.swipe('up')
            after=self.read_reel()
        except NavigationLost:
            try:self.recover_reels()
            except TransportLost:self.recover_connection()
            return
        except TransportLost:
            self.recover_connection();return
        self.back_depth=0
        changed=bool(after.get('key') and after['key']!=post.get('key'))
        self.skip_interactions_once=not changed
        self.log('interaction_video_skipped' if changed else 'interaction_skip_pending',message='互动未确认，已跳过当前视频并继续浏览' if changed else '本次翻页尚未确认，保留跳过互动标记，不会重发收藏或点赞')

    def close_collection_sheet(self,tree):
        self.observe(tree)
        button=collection_sheet_close(tree)
        if button is None:return False
        def quoted(value):return "'"+value.replace('\\','\\\\').replace("'","\\'")+"'"
        predicate="type == 'XCUIElementTypeButton' AND visible == true AND label == "+quoted(button.get('label',''))
        if button.get('name'):predicate+=' AND name == '+quoted(button.get('name'))
        found=self.command('POST','/elements',{'using':'-ios predicate string','value':predicate})
        if len(found)!=1:return False
        eid=found[0].get(ELEMENT) or found[0].get('ELEMENT')
        if not eid:return False
        try:self.command('POST','/element/'+eid+'/click',{})
        except Exception as exc:
            if transport_error(exc):raise TransportLost('关闭收藏夹浮层时连接中断，需要重新识别页面') from exc
            if isinstance(exc,ControlHTTPError) and exc.kind in ('stale element reference','no such element'):raise NavigationLost('收藏夹关闭控件已变化，重新识别后再处理') from exc
            raise
        return True

    def comment(self, post):
        if self.cfg['comments_mode']=='ai':
            return self.ai_comment(post)
        return self.rule_comment(post)

    def rule_comment(self, post):
        caption = post['caption'].casefold()
        reply = None
        for rule in self.cfg['comment_rules']:
            if any(term.casefold() in caption for term in rule['match_any']):
                options = [r for r in rule['replies'] if r not in self.daily['comment_texts']]
                if options:
                    reply = self.rng.choice(options)
                    break
        if not reply:
            return
        self.send_comment(post,reply)

    def send_comment(self,post,reply):
        if not ai_comments.english_text(reply):
            self.log('comment_skip',message='评论不是英文，已跳过')
            return
        if self.daily['posts'].get(post['key'],{}).get('comments'):
            return
        if self.daily['comments'] >= self.cfg['max_comments_per_day']:
            return
        if self.read_reel()['key'] != post['key']:
            raise RuntimeError('视频已变化，跳过评论')
        self.click('comment-button')
        self.wait(1)
        tree = self.snapshot()
        fields = [e for e in tree.iter() if e.get('visible')=='true' and e.tag in ('XCUIElementTypeTextView','XCUIElementTypeTextField')]
        if len(fields)!=1:
            raise RuntimeError('无法唯一识别评论输入框，已停止')
        field = fields[0]
        locator = field.get('name')
        if not locator:
            raise RuntimeError('评论输入框没有可靠标识，已停止')
        eid = self.find(locator)
        self.command('POST','/element/'+eid+'/click',{})
        self.command('POST','/element/'+eid+'/value',{'text':reply})
        self.wait(.5)
        tree = self.snapshot()
        send = [e for e in tree.iter() if e.get('visible')=='true' and e.tag=='XCUIElementTypeButton'
                and e.get('enabled')=='true' and e.get('label','').casefold() in ('post','post comment','send','发布','发送')]
        if len(send)!=1 or not send[0].get('name'):
            raise RuntimeError('无法唯一识别评论发送按钮，已停止；评论尚未发送')
        if not self.reserve(post,'comments',reply):
            raise RuntimeError('评论额度已用完，未发送')
        self.click(send[0].get('name'))
        self.wait(1)
        tree = self.snapshot()
        if not any(e.get('visible')=='true' and reply in e.get('label','') for e in tree.iter()):
            raise RuntimeError('评论发送结果未确认，已停止；不会自动重发')
        self.completed(post,'comments')
        self.log('comment_text', text=reply)
        self.close_comments()

    def ai_comment(self,post,preview=False):
        if not post['key']:
            self.log('ai_skip',message='无法稳定识别当前视频，跳过评论')
            return
        if any(term.casefold() in post['caption'].casefold() for term in self.cfg['exclude_keywords']):
            self.log('ai_skip',message='说明包含排除词，跳过 AI 评论')
            return
        if not preview and (self.daily['posts'].get(post['key'],{}).get('comments') or self.daily['comments']>=self.cfg['max_comments_per_day']):
            return
        if not self.model_api_key:
            if not self.ai_missing_warned or preview:
                self.log('ai_skip',message='未配置所选服务商的 API Key，跳过 AI 评论')
                self.ai_missing_warned=True
            return
        if self.daily['ai_calls']>=self.cfg['ai_max_calls_per_day']:
            self.log('ai_skip',message='今日模型调用次数已达上限，跳过')
            return
        if post['key'] in self.ai_cache and not preview:
            return
        self.ai_cache[post['key']]=None
        frames=[]
        self.log('ai_sampling',message='正在采样当前视频画面')
        for i in range(self.cfg['ai_frames']):
            if i:
                self.wait(self.cfg['ai_frame_interval'])
            if self.read_reel()['key']!=post['key']:
                self.log('ai_skip',message='采样期间视频已切换，跳过')
                return
            screenshot=self.command('GET','/screenshot')
            frames.append(ai_comments.frame_data(screenshot,self.runtime))
        if self.read_reel()['key']!=post['key']:
            self.log('ai_skip',message='视频已切换，丢弃采样')
            return
        previous=[text for day in sorted(self.ledger) for text in self.ledger[day].get('comment_texts',[])][-100:]
        self.daily['ai_calls']+=1
        atomic_json(self.ledger_path,self.ledger)
        self.log('ai_request',message='正在理解画面并生成英文评论',model=self.cfg['ai_model'])
        result_queue=queue.Queue()
        def request_model():
            try:
                result_queue.put((True,ai_comments.generate(self.cfg,post['caption'],frames,previous,self.model_api_key)))
            except Exception as exc:
                message=str(exc) if isinstance(exc,ai_comments.AIUnavailable) else '模型处理异常，已跳过'
                result_queue.put((False,message))
        threading.Thread(target=request_model,daemon=True).start()
        while True:
            self.check()
            try:
                ok,result=result_queue.get(timeout=.2)
                break
            except queue.Empty:
                continue
        if not ok:
            self.log('ai_skip',message=result)
            return
        self.ai_cache[post['key']]=result
        atomic_json(self.log_dir/('ai-'+post['key']+'.json'),result)
        self.log('ai_result',message='已生成评论' if result['accepted'] else '跳过：'+result['skip_reason'],
                 summary=result['summary'],confidence=result['confidence'],comment=result['comment'],
                 evidence=result['evidence'],accepted=result['accepted'])
        if not result['accepted']:
            return
        if preview or self.cfg['ai_send_mode']=='preview':
            self.log('ai_preview',message='仅预览，未发送评论',comment=result['comment'])
            return
        self.check()
        if self.read_reel()['key']!=post['key']:
            self.log('ai_skip',message='分析后视频已切换，未发送评论')
            return
        self.send_comment(post,result['comment'])

    def close_comments(self):
        # Dismiss only an explicitly identified Close control, never a random coordinate.
        tree=self.snapshot()
        candidates=[e for e in tree.iter() if e.get('visible')=='true' and e.tag=='XCUIElementTypeButton'
                    and e.get('label','').casefold() in ('close','close comments','dismiss','关闭')]
        if len(candidates)==1 and candidates[0].get('name'):
            label=candidates[0].get('label')
            found=self.command('POST','/elements',{'using':'-ios predicate string',
                'value':"type == 'XCUIElementTypeButton' AND visible == true AND label == "+json.dumps(label)})
            if len(found)!=1:
                raise RuntimeError('评论关闭按钮无法唯一识别，已停止')
            eid=found[0].get(ELEMENT) or found[0].get('ELEMENT')
            self.command('POST','/element/'+eid+'/click',{})
            self.wait(.5)
            self.read_reel()
        else:
            raise RuntimeError('评论已处理，请手动关闭评论面板后重新开始')

    def close_known_sheet(self, tree):
        nodes=visible_nodes(tree)
        if not any(e.get('name')=='ig-partial-modal-sheet-view-controller-content' for e in nodes):return False
        dismiss=[e for e in nodes if e.tag=='XCUIElementTypeButton' and e.get('label','').casefold()=='dismiss']
        if len(dismiss)!=1:return False
        found=self.command('POST','/elements',{'using':'-ios predicate string',
            'value':"type == 'XCUIElementTypeButton' AND visible == true AND label == 'Dismiss'"})
        if len(found)!=1:return False
        eid=found[0].get(ELEMENT) or found[0].get('ELEMENT')
        self.command('POST','/element/'+eid+'/click',{})
        return True

    def return_from_search(self):
        """Small independent navigation budget; never consumes general recovery quota."""
        if not self.cfg.get('recovery_enabled',True):
            raise RuntimeError('搜索已暂停，但自动恢复已关闭，请手动返回短视频页')
        for attempt in range(5):
            self.check()
            tree=self.snapshot();state=self.observe(tree)
            if state.kind=='reels':
                self.navigation_epoch+=1
                self.position=self.back_depth=self.stagnant=self.unchanged_swipes=0
                self.log('search_resumed',message='搜索已暂停，本轮继续短视频浏览')
                return
            if state.kind=='input_unknown':raise PageBlocked(state.reason)
            names={e.get('name') for e in visible_nodes(tree)}
            back=search_back_name(tree)
            if state.kind=='profile' and profile_back_name(tree):
                self.click(profile_back_name(tree))
            elif state.kind in ('search_input','search_context','search_results'):
                back=back or next((x for x in ('Cancel','BackButton','back-button') if x in names),None)
                if back:self.click(back)
                elif state.kind=='search_results' and 'reels-tab' in names:self.click('reels-tab')
                else:raise RuntimeError('搜索已暂停，但没有识别到安全返回入口，请手动返回短视频页')
            elif state.kind=='sheet' and self.close_known_sheet(tree):
                pass
            elif state.kind in ('explore','main','unknown') and 'reels-tab' in names:
                self.click('reels-tab')
            else:
                raise RuntimeError('搜索已暂停，当前页面无法安全返回，请手动处理')
            self.log('search_return',message='正在通过已识别入口返回短视频页',attempt=attempt+1)
            self.wait(1)
        # Validate after the last permitted navigation action.
        if self.observe(self.snapshot()).kind=='reels':
            self.navigation_epoch+=1
            self.position=self.back_depth=self.stagnant=self.unchanged_swipes=0
            self.log('search_resumed',message='搜索已暂停，本轮继续短视频浏览')
            return
        raise RuntimeError('搜索已暂停，返回短视频页的尝试已用完，请手动处理')

    def search_stage(self,stage,watch_seconds=None):
        self.check()
        self.search_step=stage
        budget=watch_seconds+10 if stage=='watch_result' else SEARCH_STAGE_SECONDS[stage]
        self.search_deadline=time.monotonic()+budget
        if self.search_overall_deadline is not None:self.search_deadline=min(self.search_deadline,self.search_overall_deadline)
        self.log('search_stage',message='搜索：'+SEARCH_STAGE_LABELS[stage],stage=stage,budget_seconds=round(budget,1))

    def search_ready(self):
        if self.feature_state['search']['enabled']:return True
        if self.search_retry_at is None or time.monotonic()<self.search_retry_at:return False
        self.search_retry_at=None
        if self.search_attempts>=self.cfg['max_searches_per_run']:
            self.feature_state['search']={'enabled':False,'reason':'本轮搜索次数上限已到，继续浏览'}
            self.log('feature_disabled',message='本轮搜索次数上限已到，继续短视频浏览',feature='search')
            return False
        self.feature_state['search']={'enabled':True,'reason':''}
        self.log('search_reenabled',message='搜索冷却结束，允许按计划尝试下一次搜索')
        return True

    def search_optional(self, term):
        if not self.search_ready():return False
        self.active_search_term=term
        self.search_deadline=None
        budget=search_budget_seconds(self.cfg);self.search_overall_deadline=time.monotonic()+budget
        self.log('search_budget',message='搜索使用独立阶段预算，正常观看不占用页面跳转预算',total_seconds=budget)
        try:
            self.search(term)
            self.search_consecutive_failures=0;self.search_retry_at=None
            self.feature_state['search']={'enabled':True,'reason':''}
            return True
        except (Halt,PageBlocked):raise
        except Exception as exc:
            self.search_deadline=None;self.search_overall_deadline=None
            self.search_failures+=1;self.search_consecutive_failures+=1
            self.feature_state['search']={'enabled':False,'reason':'搜索失败，正在恢复视频浏览'}
            self.diagnose('search',exc)
            self.log('search_recovering',message='本次搜索已放弃，正在确认能否继续短视频浏览',reason=diagnostics.redact(str(exc)))
            while True:
                try:
                    # Always re-observe before navigating. Never replay search
                    # input or an uncertain action from the failed search.
                    self.return_from_search()
                    break
                except TransportLost:self.wait_for_page_response()
                except NavigationLost as recovery:raise RuntimeError('搜索恢复未能确认视频页面，请检查手机页面后重新开始') from recovery
            # Schedule from successful recovery, never from the start of a long search.
            if self.search_consecutive_failures>=3 or self.search_attempts>=self.cfg['max_searches_per_run']:
                self.search_retry_at=None
                reason='连续三次搜索失败，本轮停用搜索，继续浏览' if self.search_consecutive_failures>=3 else '本轮搜索次数上限已到，继续浏览'
                self.feature_state['search']={'enabled':False,'reason':reason}
                self.log('feature_disabled',message=reason,feature='search')
            else:
                delay=self.rng.uniform(180,360) if self.search_consecutive_failures==1 else self.rng.uniform(360,600)
                self.search_retry_at=time.monotonic()+delay
                reason='搜索冷却中，约 '+str(round(delay))+' 秒后重试；继续浏览'
                self.feature_state['search']={'enabled':False,'reason':reason,'cooldown':True}
                self.log('search_cooldown',message=reason,retry_seconds=round(delay),consecutive_failures=self.search_consecutive_failures)
            return False
        finally:
            self.search_deadline=None;self.search_overall_deadline=None
            self.active_search_term=None

    def search_input(self):
        for attempt in range(3):
            tree=self.snapshot()
            if self.observe(tree).kind=='input_unknown':raise PageBlocked('页面包含未知输入内容，已保留，请手动处理')
            field=search_input_node(tree)
            if field is not None:
                found=self.command('POST','/elements',{'using':'-ios predicate string','value':
                    'type == '+json.dumps(field.tag)+' AND name == '+json.dumps(field.get('name'))+' AND visible == true AND enabled == true'})
                if len(found)==1:return found[0].get(ELEMENT) or found[0].get('ELEMENT')
            if attempt<2:self.wait(.7)
        raise NavigationLost('未能唯一识别顶部搜索输入区，尝试返回 Reels')

    def open_search_entry(self):
        # Search/profile viewers can hide the bottom tabs. Follow only observed
        # navigation controls; never back out of an unknown form or modal.
        for _ in range(5):
            tree=self.snapshot();state=self.observe(tree);nodes=visible_nodes(tree)
            buttons=[e for e in nodes if e.tag=='XCUIElementTypeButton' and e.get('enabled','true')=='true']
            if sum(e.get('name')=='explore-tab' for e in buttons)==1:
                self.click('explore-tab');return
            if state.kind in ('input_unknown','sheet','unknown'):raise NavigationLost('搜索入口被未知页面或输入内容遮挡，已保留页面')
            back=profile_back_name(tree)
            if state.kind in ('search_input','search_context','search_results'):back=search_back_name(tree)
            names={e.get('name') for e in nodes}
            if state.kind=='reels' and {'reels-viewer','sundial-viewer-video-collection-view'}<=names and sum(e.get('name')=='back-button' for e in buttons)==1:
                back='back-button'
            if not back:raise NavigationLost('当前页面没有可确认的搜索入口或返回按钮')
            self.click(back);self.wait(.5)
            self.log('search_entry_back',message='已通过可识别的返回入口查找搜索导航')
        raise NavigationLost('返回搜索入口的尝试已用完，暂停本轮搜索')

    def search(self, term):
        if not ai_comments.english_text(term):
            self.log('search_skip',message='搜索词不是英文，已跳过')
            return
        self.search_attempts+=1
        self.search_stage('open_explore')
        if self.find('explore-tab',optional=True):self.click('explore-tab')
        else:self.open_search_entry()
        self.wait(.7)
        self.search_stage('locate_input')
        eid=self.search_input()
        self.command('POST','/element/'+eid+'/click',{})
        self.wait(.5)
        self.search_stage('focus_input')
        eid=self.search_input()
        self.search_stage('submit_query')
        self.command('POST','/element/'+eid+'/clear',{})
        self.command('POST','/element/'+eid+'/value',{'text':term+'\n'})
        self.wait(2)
        self.search_stage('verify_results')
        tree=self.search_results(term)
        self.searches+=1
        self.log('search',message='已搜索：'+term,layout=search_layout(tree,term),attempt=self.search_attempts)
        seen=set()
        return_started=None
        for index in range(int(self.cfg['search_videos_per_query'])):
            self.search_stage('select_result')
            tree=self.search_results(term)
            nodes=[e for e in tree.iter() if e.get('visible')=='true']
            candidates=[e for e in nodes if e.tag=='XCUIElementTypeCell'
                and e.get('name')=='media-discovery-cell' and e.get('label','').startswith('Video by ')
                and e.get('label') not in seen and float(e.get('y',0))>=143
                and float(e.get('y',0))+float(e.get('height',0))<=850]
            if not candidates:
                self.log('search_skip',message='当前结果没有新的、可识别的视频，结束本次搜索浏览')
                break
            label=candidates[0].get('label');seen.add(label)
            found=self.command('POST','/elements',{'using':'-ios predicate string','value':
                "type == 'XCUIElementTypeCell' AND name == 'media-discovery-cell' AND visible == true AND label == "+json.dumps(label)})
            if len(found)!=1:
                self.log('search_skip',message='搜索视频控件不唯一，跳过')
                break
            self.search_stage('open_video')
            self.command('POST','/element/'+found[0][ELEMENT]+'/click',{})
            self.wait(1.5)
            post=self.read_reel()
            self.search_videos+=1;self.counts['watched']+=1
            self.log('search_watch',message='观看搜索结果视频',query=term,author=post['author'],search_videos=self.search_videos)
            watch_seconds=self.rng.uniform(self.cfg['search_watch_min'],self.cfg['search_watch_max'])
            self.search_stage('watch_result',watch_seconds)
            self.wait(watch_seconds)
            self.search_stage('return_results')
            # Search browsing never sends social interactions. Return through the observed viewer control.
            return_started=time.monotonic()
            current=self.read_reel()
            if 'back-button' not in {e.get('name') for e in current['nodes']}:
                raise NavigationLost('搜索视频缺少可识别的返回入口')
            self.click('back-button');self.wait(1)
            tree=self.search_results(term)
            if index+1<int(self.cfg['search_videos_per_query']):
                for _ in range(int(self.cfg['search_result_scrolls'])):
                    self.search_stage('scroll_results')
                    self.search_results(term)
                    self.gesture('up');self.wait(1)
                    tree=self.search_results(term)
                    self.log('search_scroll',message='已滚动并重新确认搜索结果',query=term)
        self.search_stage('return_reels')
        self.reels(initial_tree=tree)
        self.log('search_return_complete',message='搜索浏览结束，已恢复短视频浏览',
                 seconds=round(time.monotonic()-return_started,3) if return_started is not None else None)
        self.search_step=None

    def search_results(self,term):
        for attempt in range(4):
            tree=self.snapshot()
            if search_layout(tree,term):return tree
            if attempt<3:self.wait(1)
        raise NavigationLost('搜索结果页面未能确认，返回浏览入口')


    def gesture(self, direction, allow_cached_rect=False):
        if direction not in ('up','down'):raise ValueError('仅支持竖向滑动')
        self._gesture_action_attempted=False
        cached=getattr(self,'_gesture_rect_cache',None)
        if allow_cached_rect and cached and cached[0]==self.path:
            rect=cached[1]
        else:rect=self.command('GET','/window/rect')
        self._gesture_rect_cache=(self.path,rect)
        w,h=rect['width'],rect['height'];c=self.cfg
        distance=self.rng.uniform(c['gesture_distance_min'],c['gesture_distance_max'])/100*h
        top,bottom=c['gesture_top']/100*h,c['gesture_bottom']/100*h
        low=self.rng.uniform(top,bottom-distance);high=low+distance
        x=self.rng.uniform(c['gesture_left'],c['gesture_right'])/100*w
        y1,y2=(high,low) if direction=='up' else (low,high)
        duration=round(self.rng.uniform(c['gesture_duration_min'],c['gesture_duration_max']))
        x,y1,y2=round(x),round(y1),round(y2)
        # One vertical stroke, inside the configured central safe rectangle.
        self._gesture_action_attempted=True
        self.command('POST','/actions',{'actions':[{'type':'pointer','id':'finger',
            'parameters':{'pointerType':'touch'},'actions':[
            {'type':'pointerMove','duration':0,'origin':'viewport','x':x,'y':y1},
            {'type':'pointerDown','button':0},{'type':'pause','duration':50},
            {'type':'pointerMove','duration':duration,'origin':'viewport','x':x,'y':y2},
            {'type':'pointerUp','button':0}]}]})
        self.log('gesture',message='已完成中央区域竖向滑动',direction=direction,
                 start=[x,y1],end=[x,y2],duration_ms=duration)

    def wait_for_page_response(self):
        """Wait without UI writes; reconnect only after repeated independent failures."""
        failures=0
        while True:
            self.check()
            try:
                old=getattr(self,'watch_read_deadline',None)
                try:
                    self.watch_read_deadline=time.monotonic()+15
                    tree=self.snapshot()
                    self.observe(tree)
                finally:self.watch_read_deadline=old
                self.log('page_response_restored',message='页面读取已恢复，保留当前连接')
                return tree
            except (Halt,PageBlocked):raise
            except Exception as exc:
                if not isinstance(exc,(TransportLost,WatchBudgetExpired)) and not transport_error(exc):raise
            if self.control_available():failures=0
            else:failures+=1
            if failures>=2:
                self.log('control_unresponsive',message='页面读取及不同控制检查连续无响应，进入重连')
                self.recover_connection()
                return None
            self.log('page_response_waiting',message='页面读取待确认，暂不重建连接，不重复发送动作',failed_checks=failures)
            self.wait(5)

    def advance_after_read_timeout(self,before):
        """One normal swipe from the last verified Reel; never resend on timeout."""
        self.skip_interactions_once=True
        uncertain=False
        try:self.gesture('up',allow_cached_rect=True)
        except Exception as exc:
            if not isinstance(exc,TransportLost) and not transport_error(exc):raise
            if not getattr(self,'_gesture_action_attempted',False):
                self.wait_for_page_response()
                return
            uncertain=True
            self.log('swipe_pending',message='滑动结果待确认，等待页面读回，不重复发送滑动')
        if not uncertain:
            self.swipes+=1;self.position+=1
            self.log('swipe',direction='up',message='进度读取异常后已执行一次翻页，等待视频信息确认')
        self.wait(2)
        # A late response may arrive after the action request timed out. Only
        # read from here onward; no gesture retries or social interactions.
        while True:
            self.check()
            try:
                old=getattr(self,'watch_read_deadline',None)
                try:
                    self.watch_read_deadline=time.monotonic()+15
                    after=self.read_reel()
                finally:self.watch_read_deadline=old
            except (TransportLost,WatchBudgetExpired):
                tree=self.wait_for_page_response()
                if tree is None:return
                continue
            if not before.get('key') or not after.get('key'):
                self.log('swipe_pending',message='尚无可比较的视频标识，继续等待，不重复滑动')
                self.wait(5);continue
            changed=before['key']!=after['key']
            if uncertain and not changed:
                self.log('swipe_pending',message='滑动响应未确认且视频标识未变化，继续等待，不重复发送')
                self.wait(5);continue
            if uncertain:self.swipes+=1;self.position+=1
            self.last_swipe_post=after;self.skip_interactions_once=True
            self.log('swipe_verified' if changed else 'swipe_unchanged',
                     message='已通过视频控件确认切页，继续浏览' if changed else '滑动已响应，视频尚未变化，保留页面继续浏览')
            return

    def swipe(self, direction):
        before=self.read_reel()
        transition_started=time.monotonic()
        self.gesture(direction)
        self.swipes+=1
        self.position=max(0,self.position+(1 if direction=='up' else -1))
        self.log('swipe',direction=direction,message='回看上一条' if direction=='down' else '切换下一条')
        self.wait(self.rng.uniform(.8,1.5))
        after=self.read_reel()
        if before['key'] and after['key']==before['key']:
            # A delayed accessibility tree or one short stroke is not navigation loss.
            self.wait(2)
            after=self.read_reel()
        if before['key'] and after['key']==before['key']:
            self.unchanged_swipes+=1
            self.log('swipe_unchanged',message='本次未确认切换视频，保留页面并等待下一次翻页',consecutive=self.unchanged_swipes)
            if self.unchanged_swipes>=3:
                self.refresh_required=True
                raise NavigationLost('连续三次滑动仍是同一视频，尝试恢复入口')
        else:
            self.unchanged_swipes=0
            if after.get('key') and getattr(self,'_visible_video_key',None)==after['key']:
                self._visible_video_since=min(self._visible_video_since,transition_started)
        self.last_swipe_post=after
        self.log('page_checked',message='滑动后已确认仍在 Reels')

    def run(self):
        lock=(self.runtime/'runner.lock').open('w')
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            self.log('error',message='另一个浏览任务正在运行')
            self.log('finished',message='运行已结束',status='error',log_dir=str(self.log_dir))
            return 'error'
        status='complete'
        device_guard=None
        try:
            self.runtime.joinpath('STOP').unlink(missing_ok=True)
            device_guard=lock_device(self.cfg['device_udid'])
            try:
                self.connect()
                self.reels()
            except NavigationLost:
                self.recover_reels()
            except TransportLost:
                self.recover_connection()
            self.deadline=time.monotonic()+self.seconds
            self.log('started',message='开始浏览',minutes=round(self.seconds/60,2))
            if not self.browse_only and self.cfg['comments_mode']=='ai' and self.cfg['enable_comments'] and not self.model_api_key:
                self.log('ai_skip',message='未配置 API Key，继续浏览并跳过 AI 评论')
                self.ai_missing_warned=True
            if self.analysis_only:
                self.ai_comment(self.read_reel(),preview=True)
                raise Halt('当前视频预览已完成，未发送评论')
            self.pause_enabled=True
            next_search=time.monotonic()+self.rng.uniform(45,90)
            last_key=None
            while True:
                try:
                    try:
                        self.check()
                        post=self.read_reel()
                        self.current_post_key=post['key']
                        skip_interaction=self.skip_interactions_once or post['key'] in self.handoff_skip_keys
                        self.skip_interactions_once=False
                        self.counts['watched']+=1
                        related=self.related(post)
                        viewing_related=self.watch_related(post)
                        self.log('watch',message='观看相关视频' if viewing_related else '随机短看后跳过',author=post['author'],related=viewing_related)
                        if post['key'] and post['key']==last_key:
                            self.stagnant+=1
                        else:
                            self.stagnant=0
                        if self.stagnant>=3:
                            self.refresh_required=True
                            raise NavigationLost('连续翻页后仍停留同一内容')
                        last_key=post['key']
                        if self.watch_video(post,viewing_related) is True:
                            self.advance_after_read_timeout(post)
                            last_key=None
                            continue
                        if skip_interaction:self.log('handoff_skip',message='人工接管后的当前视频跳过互动')
                        if not skip_interaction:self.consider_like(post,related)
                        self.check()
                        if related and not self.browse_only and not skip_interaction:
                            self.in_interaction=True
                            if self.cfg['enable_saves'] and self.rng.random()<self.cfg['save_probability']:
                                self.toggle_on(post,'saves','save-button',{'save','save post'})
                            if self.cfg['enable_comments'] and self.cfg['comments_mode']!='ai' and self.rng.random()<self.cfg['comment_probability']:
                                self.comment(post)
                            self.in_interaction=False
                        if (not skip_interaction and not self.browse_only and self.cfg['enable_comments'] and self.cfg['comments_mode']=='ai'
                            and self.rng.random()<self.cfg['comment_probability']):
                            self.in_interaction=True
                            self.ai_comment(post)
                            self.in_interaction=False
                        if (not self.browse_only and self.cfg['enable_search'] and self.cfg['keywords']
                            and self.search_ready() and self.search_attempts<self.cfg['max_searches_per_run'] and time.monotonic()>=next_search):
                            try:self.search_optional(self.rng.choice(self.cfg['keywords']))
                            finally:next_search=time.monotonic()+self.rng.uniform(180,360)
                            last_key=None
                            self.back_depth=0
                            self.position=0
                            continue
                        if self.back_depth==0 and self.position>=2 and self.rng.random()<.1:
                            burst=self.rng.choices([1,2],[.8,.2])[0]
                            for _ in range(min(burst,self.position)):
                                self.check()
                                self.swipe('down')
                                self.wait(self.rng.uniform(2,5))
                            self.back_depth=burst
                        else:
                            burst=self.rng.choices([1,2,3],[.8,.16,.04])[0]
                            advanced=0
                            for _ in range(burst):
                                self.check()
                                self.last_swipe_post=None
                                self.swipe('up');advanced+=1
                                if self.last_swipe_post and self.watch_related(self.last_swipe_post):
                                    self.log('watch_topic_found',message='连续翻页遇到相关内容，停下重新安排观看')
                                    break
                                if burst>1:
                                    self.wait(self.rng.uniform(1.5,3.5))
                            self.back_depth=max(0,self.back_depth-advanced)
                    except InteractionSkipped as exc:
                        # Preserve the pending ledger reservation and unwind the
                        # interaction before recovering navigation or transport.
                        self.in_interaction=False
                        self.skip_interactions_once=True
                        self.diagnose('interaction',exc)
                        self.log('interaction_skipped',message='已跳过本条互动，不会重复点击；重新识别后继续浏览',detail=str(exc))
                        self.continue_after_interaction(post)
                        last_key=None
                        continue
                    except TransportLost:
                        if self.in_interaction:raise RuntimeError('互动结果未确认，已停止且不会重发')
                        self.skip_interactions_once=True
                        self.wait_for_page_response()
                        last_key=None
                        continue
                    except NavigationLost as exc:
                        if self.in_interaction:raise RuntimeError('互动时页面变化，已停止以避免误操作') from exc
                        self.diagnose('navigation',exc)
                        self.log('navigation_lost',message=str(exc))
                        try:self.recover_reels()
                        except TransportLost:self.recover_connection()
                        last_key=None
                        continue
                except ManualResume:
                    self.in_interaction=False;last_key=None;self.back_depth=0;self.position=0
                    next_search=time.monotonic()+self.rng.uniform(45,90)
                    continue
        except Halt as e:
            self.pause_enabled=False
            self.log('stopped',message=str(e))
            status='stopped' if self.stop.is_set() or (self.runtime/'STOP').exists() else 'complete'
        except Exception as e:
            self.pause_enabled=False
            self.diagnose('fatal',e)
            status='error'
            message='手机控制连接中断，当前动作结果未确认，已停止且不会重发' if transport_error(e) else (str(e) or type(e).__name__)
            self.log('error',message=message,detail=str(e)[:1800])
        finally:
            self.pause_enabled=False
            if self.managed and self.path:
                try:
                    self.http('DELETE',self.path,timeout=8)
                    (self.runtime/'active-session.json').unlink(missing_ok=True)
                except Exception:self.log('session_cleanup_pending',message='此设备连接清理未完成，下一次启动会重新检查。')
            summary={'status':status,'counts':self.counts,'swipes':self.swipes,'searches':self.searches,
                     'log_dir':str(self.log_dir),'run_id':self.run_id,'recoveries':self.recoveries,
                     'reconnections':self.reconnections,'search_videos':self.search_videos,'search_attempts':self.search_attempts,
                     'feature_state':self.feature_state,'search_failures':self.search_failures,'search_consecutive_failures':self.search_consecutive_failures,
                     'page_state':self.page_state.kind if self.page_state else 'unknown'}
            try:
                atomic_json(self.log_dir/'summary.json',summary)
                atomic_json(self.runtime/'last-run.json',summary)
            finally:
                fcntl.flock(lock,fcntl.LOCK_UN)
                lock.close()
                if device_guard:device_guard.close()
            self.log('finished',message='运行已结束',**summary)
        return status

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--seconds',type=float)
    p.add_argument('--browse-only',action='store_true')
    args=p.parse_args()
    cfg=load_config()
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    result=Runner(cfg,stop=stop,seconds=args.seconds,browse_only=args.browse_only).run()
    raise SystemExit(1 if result=='error' else 0)

if __name__=='__main__':
    main()
