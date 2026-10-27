#!/usr/bin/env python3
import json
import queue
import subprocess
import sys
import threading
import os
import time
import traceback
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import diagnostics
import ui_theme as ui
from pathlib import Path
from runner import ROOT, Runner, atomic_json, AUTOMATION_DEFAULTS, validate_motion
from ai_comments import DEFAULTS, PROVIDERS, english_text
from app_paths import FROZEN,DATA,load_config

class Panel:
    def __init__(self, root, config=None, config_path=None):
        self.root=root
        self.cfg={**AUTOMATION_DEFAULTS,**DEFAULTS,**(config if config is not None else load_config())}
        self.config_path=Path(config_path) if config_path else ROOT/'config.json'
        self.pinned_device=config is not None
        self.batch_mode=False
        self.device_window=None
        self.signature_notices=set()
        self.events=queue.Queue()
        self.worker=None
        self.generation=0
        self.run_ui_active=False
        self.pending_start=None
        self.worker_result=None
        self.finished_row=None
        self.run_error='任务未返回正常结束状态'
        self.last_start={}
        self.phase_text=''
        self.stop_requested_at=None
        self.stop_event=threading.Event()
        self.closing=False
        self.comment_rules=json.loads(json.dumps(self.cfg['comment_rules']))
        self.selected_rule=None
        ui.apply(root);root.title('Instagram 控制台')
        root.geometry(f'940x{min(820,root.winfo_screenheight()-100)}');root.minsize(820,580)
        outer=ttk.Frame(root,padding=20);outer.pack(fill='both',expand=True)
        self.title_name=tk.StringVar(value=self.cfg.get('device_name') or '设备控制台')
        ttk.Label(outer,textvariable=self.title_name,style='Title.TLabel').pack(anchor='w')
        self.device_label=tk.StringVar(value=('iOS '+self.cfg.get('device_ios','未知')+' · 手动运行'))
        ttk.Label(outer,textvariable=self.device_label,style='Muted.TLabel').pack(anchor='w',pady=(3,5))
        device_row=ttk.Frame(outer);device_row.pack(fill='x',pady=(0,6))
        self.signature_status=tk.StringVar(value='签名状态待检查')
        ttk.Label(device_row,textvariable=self.signature_status,font=('PingFang SC',11)).pack(side='left')
        self.device_button=ttk.Button(device_row,text='设备配置 / 重新签名',command=self.open_devices)
        self.device_button.pack(side='right')
        state_frame=ttk.Frame(outer); state_frame.pack(fill='x')
        self.status=tk.StringVar(value='已停止，点击开始即可运行')
        self.status_display=ttk.Label(state_frame,textvariable=self.status,style='Status.TLabel',wraplength=840)
        self.status_display.pack(fill='x')
        self.feature_status=tk.StringVar(value='搜索状态：按本轮设置启用；异常时暂停搜索，确认页面后继续浏览')
        self.feature_display=ttk.Label(state_frame,textvariable=self.feature_status,wraplength=840,style='Muted.TLabel')
        self.feature_display.pack(fill='x')
        self.stats=tk.StringVar(value='浏览 0   ·   翻页 0   ·   点赞 0   ·   收藏 0   ·   评论 0   ·   搜索 0')
        ttk.Label(state_frame,textvariable=self.stats,font=('PingFang SC',11)).pack(anchor='w',pady=(6,10))
        actions=ttk.Frame(outer); actions.pack(fill='x',pady=(0,12))
        self.start_button=ttk.Button(actions,text='开始浏览',style='Primary.TButton',command=self.start)
        self.start_button.pack(side='left')
        self.stop_button=ttk.Button(actions,text='停止运行',style='Action.TButton',command=self.stop,state='disabled')
        self.stop_button.pack(side='left',padx=10)
        self.pause_button=ttk.Button(actions,text='暂停托管',command=self.pause_hosting,state='disabled');self.pause_button.pack(side='left',padx=(0,8))
        self.resume_button=ttk.Button(actions,text='继续托管',command=self.resume_hosting,state='disabled');self.resume_button.pack(side='left',padx=(0,8))
        self.test_button=ttk.Button(actions,text='试运行 45 秒（仅浏览）',command=lambda:self.start(test=True))
        self.test_button.pack(side='left',padx=6)
        footer=ttk.Frame(outer);footer.pack(side='bottom',fill='x',pady=(12,0))
        self.edit_hint=tk.StringVar(value='仅此设备   ·   ⌘ Return 开始   ·   Esc 停止')
        ttk.Label(footer,textvariable=self.edit_hint,style='Muted.TLabel',font=('PingFang SC',10)).pack(side='left')
        self.global_save_button=ttk.Button(footer,text='保存全部设置',command=self.save_settings)
        self.global_save_button.pack(side='right')
        self.tabs=ttk.Notebook(outer);self.tabs.enable_traversal(); self.tabs.pack(fill='both',expand=True)
        settings_page=ttk.Frame(self.tabs)
        comments_page=ttk.Frame(self.tabs)
        settings=ui.scroll_form(settings_page)
        comments=ui.scroll_form(comments_page)
        logs=ttk.Frame(self.tabs,padding=12)
        self.tabs.add(settings_page,text='浏览设置')
        self.tabs.add(comments_page,text='评论规则')
        self.tabs.add(logs,text='运行日志')
        self.settings_widgets=[self.global_save_button]
        self.minutes=tk.StringVar(value=str(self.cfg['minutes']))
        row=ttk.Frame(settings); row.pack(fill='x',pady=(0,16))
        ttk.Label(row,text='本次时长上限（分钟）').pack(side='left')
        spin=ttk.Spinbox(row,from_=1,to=120,textvariable=self.minutes,width=5)
        spin.pack(side='left',padx=10); self.settings_widgets.append(spin)
        ttk.Label(row,text='随时可以提前停止').pack(side='left')
        ttk.Label(settings,text='英文搜索关键词，用逗号分隔').pack(anchor='w')
        self.keywords=tk.StringVar(value='，'.join(self.cfg['keywords']))
        entry=ttk.Entry(settings,textvariable=self.keywords); entry.pack(fill='x',pady=(5,12)); self.settings_widgets.append(entry)
        ttk.Label(settings,text='内容匹配词，支持中英文，用逗号分隔').pack(anchor='w')
        self.terms=tk.StringVar(value='，'.join(self.cfg['relevance_terms']))
        entry=ttk.Entry(settings,textvariable=self.terms); entry.pack(fill='x',pady=(5,12)); self.settings_widgets.append(entry)
        ttk.Label(settings,text='不互动的内容词，用逗号分隔').pack(anchor='w')
        self.excludes=tk.StringVar(value='，'.join(self.cfg['exclude_keywords']))
        entry=ttk.Entry(settings,textvariable=self.excludes); entry.pack(fill='x',pady=(5,16)); self.settings_widgets.append(entry)
        flags=ttk.Frame(settings); flags.pack(fill='x',pady=(0,14))
        self.flags={}
        for key,label in [('enable_search','搜索'),('enable_likes','点赞'),('enable_saves','收藏'),('enable_comments','英文评论')]:
            self.flags[key]=tk.BooleanVar(value=self.cfg[key])
            w=ttk.Checkbutton(flags,text=label,variable=self.flags[key]); w.pack(side='left',padx=(0,18)); self.settings_widgets.append(w)
        cadence=ttk.Frame(settings);cadence.pack(fill='x',pady=(0,8))
        ttk.Label(cadence,text='点赞间隔：每浏览').pack(side='left')
        self.like_interval_min=tk.StringVar(value=str(self.cfg['like_interval_min']))
        self.like_interval_max=tk.StringVar(value=str(self.cfg['like_interval_max']))
        for label,var in [('',self.like_interval_min),('～',self.like_interval_max)]:
            if label:ttk.Label(cadence,text=label).pack(side='left')
            w=ttk.Spinbox(cadence,from_=1,to=100,textvariable=var,width=4);w.pack(side='left',padx=6);self.settings_widgets.append(w)
        ttk.Label(cadence,text='条视频，随机安排一次').pack(side='left')
        ttk.Label(settings,text='到达间隔后，遇到内容匹配且未点过赞的视频才点赞；回看同一视频不重复计数。每日上限单独生效。',style='Muted.TLabel',wraplength=680).pack(anchor='w',pady=(0,12))
        limits=ttk.Frame(settings); limits.pack(fill='x')
        self.limits={}
        for key,label in [('likes','每日点赞上限'),('saves','每日收藏上限'),('comments','每日评论上限')]:
            ttk.Label(limits,text=label).pack(side='left')
            var=tk.StringVar(value=str(self.cfg['max_'+key+'_per_day'])); self.limits[key]=var
            w=ttk.Spinbox(limits,from_=0,to=50,textvariable=var,width=4); w.pack(side='left',padx=(6,16)); self.settings_widgets.append(w)
        recovery=ttk.Frame(settings);recovery.pack(fill='x',pady=(12,0))
        self.recovery_enabled=tk.BooleanVar(value=self.cfg.get('recovery_enabled',True))
        w=ttk.Checkbutton(recovery,text='页面异常自动恢复',variable=self.recovery_enabled)
        w.pack(side='left',padx=(0,14));self.settings_widgets.append(w)
        self.recovery_attempts=tk.StringVar(value=str(self.cfg.get('recovery_attempts',3)))
        self.recovery_limit=tk.StringVar(value=str(self.cfg.get('recovery_run_limit',5)))
        for label,var,high in [('每次尝试',self.recovery_attempts,5),('5 分钟内上限',self.recovery_limit,20)]:
            ttk.Label(recovery,text=label).pack(side='left')
            w=ttk.Spinbox(recovery,from_=1,to=high,textvariable=var,width=3);w.pack(side='left',padx=(5,12));self.settings_widgets.append(w)
        ttk.Label(settings,text='观看时间会变化，偶尔回看或连续翻页。点赞、收藏按说明匹配。\nAI 评论会另结合采样画面；断连或无法确认操作结果时停止。',wraplength=690).pack(anchor='w',pady=(20,0))
        ttk.Label(comments,text='用于“固定规则”模式；AI 生成请在“AI 评论”中配置。',wraplength=680).pack(anchor='w')
        ttk.Label(comments,text='依据视频说明匹配，不解析视频画面。每条评论每天最多使用一次。',wraplength=680).pack(anchor='w',pady=(4,10))
        self.rule_list=tk.Listbox(comments,height=3,exportselection=False)
        self.rule_list.pack(fill='x',pady=(0,10))
        self.rule_list.bind('<<ListboxSelect>>',self.select_rule)
        self.rule_name=tk.StringVar()
        self.rule_terms=tk.StringVar()
        ttk.Label(comments,text='规则名称').pack(anchor='w')
        w=ttk.Entry(comments,textvariable=self.rule_name); w.pack(fill='x',pady=(3,7)); self.settings_widgets.append(w)
        ttk.Label(comments,text='视频说明包含任一触发词，用逗号分隔').pack(anchor='w')
        w=ttk.Entry(comments,textvariable=self.rule_terms); w.pack(fill='x',pady=(3,7)); self.settings_widgets.append(w)
        ttk.Label(comments,text='允许发送的英文评论，每行一句').pack(anchor='w')
        self.rules=tk.Text(comments,height=3,wrap='word',font=('PingFang SC',12),undo=True)
        self.rules.pack(fill='both',expand=True,pady=(3,7))
        rule_actions=ttk.Frame(comments); rule_actions.pack(fill='x')
        for label,action in [('新增规则',self.new_rule),('删除当前规则',self.delete_rule)]:
            w=ttk.Button(rule_actions,text=label,command=action); w.pack(side='left',padx=(0,8)); self.settings_widgets.append(w)
        self.save_button=ttk.Button(comments,text='保存规则和设置',command=self.save_settings)
        self.save_button.pack(anchor='e',pady=(7,0)); self.settings_widgets.append(self.save_button)
        self.refresh_rules(0)
        log_body=ttk.Frame(logs)
        self.log_text=tk.Text(log_body,height=15,wrap='word',font=('PingFang SC',11),state='disabled')
        log_footer=ttk.Frame(logs);log_footer.pack(side='bottom',fill='x',pady=(8,0))
        log_body.pack(fill='both',expand=True)
        log_scroll=ttk.Scrollbar(log_body,orient='vertical',command=self.log_text.yview)
        log_scroll.pack(side='right',fill='y');self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side='left',fill='both',expand=True)
        export_row=ttk.Frame(log_footer);export_row.pack(fill='x')
        self.include_diagnostic_images=tk.BooleanVar(value=False)
        ttk.Checkbutton(export_row,text='导出时包含已记录截图（可能含账号及内容）',variable=self.include_diagnostic_images).pack(side='left')
        ttk.Button(export_row,text='导出诊断包',command=self.export_diagnostics).pack(side='right')
        ttk.Button(log_footer,text='打开日志文件夹',command=self.open_logs).pack(anchor='e',pady=(8,0))
        self.build_ai_settings()
        self.build_motion_settings()
        self.build_search_settings()

        root.protocol('WM_DELETE_WINDOW',self.close)
        root.tk.createcommand('tk::mac::Quit',self.close)
        root.bind('<Configure>',lambda e:self.resize_header(e),add='+')
        root.bind('<Escape>',lambda _:self.stop() if self.worker and self.worker.is_alive() else None)
        root.bind('<Command-Return>',lambda _:self.start())
        root.bind('<Command-s>',lambda _:self.global_save_button.invoke())
        root.bind('<Control-Tab>',lambda _:self.cycle_tab(1))
        root.bind('<Control-Shift-Tab>',lambda _:self.cycle_tab(-1))
        root.bind('<ButtonPress-1>',self.trace_pointer,add='+')
        root.bind('<ButtonRelease-1>',self.trace_pointer,add='+')
        root.after(200,self.poll)
        root.after(400,self.signature_tick)
        if FROZEN and not self.cfg.get('device_udid'):root.after(650,self.open_devices)

    def cycle_tab(self,direction):
        self.tabs.select((self.tabs.index(self.tabs.select())+direction)%len(self.tabs.tabs()))
        self.tabs.focus_set()
        return 'break'

    def resize_header(self,event):
        if event.widget==self.root:
            width=max(600,event.width-48)
            self.status_display.configure(wraplength=width);self.feature_display.configure(wraplength=width)

    def build_search_settings(self):
        page=ttk.Frame(self.tabs,padding=12)
        self.tabs.add(page,text='搜索设置')
        footer=ttk.Frame(page);footer.pack(side='bottom',fill='x',pady=(8,0))
        area=ttk.Frame(page);area.pack(fill='both',expand=True)
        canvas=tk.Canvas(area,highlightthickness=0,borderwidth=0)
        scrollbar=ttk.Scrollbar(area,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        frame=ttk.Frame(canvas,padding=4)
        window=canvas.create_window((0,0),window=frame,anchor='nw')
        frame.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        def resize(event):
            canvas.itemconfigure(window,width=event.width)
            for child in frame.winfo_children():
                if isinstance(child,ttk.Label):child.configure(wraplength=max(220,event.width-16))
        canvas.bind('<Configure>',resize)
        ui.bind_form_scroll(self.root,canvas,frame)
        ttk.Label(frame,text='搜索后打开可识别的视频，观看后返回结果，再滚动查找下一条。',wraplength=680).pack(anchor='w',pady=(0,16))
        self.search_numbers={}
        for key,label,low,high in [('search_videos_per_query','每次搜索观看视频数',1,5),
            ('search_result_scrolls','两条结果之间滚动次数',1,3),
            ('search_watch_min','每条最短观看秒数',5,60),('search_watch_max','每条最长观看秒数',5,90)]:
            row=ttk.Frame(frame);row.pack(fill='x',pady=6)
            ttk.Label(row,text=label,width=26).pack(side='left')
            var=tk.StringVar(value=str(self.cfg[key]));self.search_numbers[key]=var
            widget=ttk.Spinbox(row,from_=low,to=high,textvariable=var,width=5)
            widget.pack(side='left');self.settings_widgets.append(widget)
        ttk.Label(frame,text='搜索结果视频仅观看。遇到无法识别的页面会尝试返回；出现验证或输入草稿时停止。\n恢复额度按最近 5 分钟计算，单次没有翻动会先等待复查，连续三次不变才恢复。',wraplength=680).pack(anchor='w',pady=(16,16))
        self.diagnostic_images=tk.BooleanVar(value=self.cfg.get('diagnostic_screenshots',False))
        widget=ttk.Checkbutton(frame,text='异常时在本机记录页面截图（可能含账号及内容，默认不导出）',variable=self.diagnostic_images)
        widget.pack(anchor='w',pady=(0,8));self.settings_widgets.append(widget)
        ttk.Label(frame,text='搜索异常后，本轮暂停搜索；只有确认返回短视频页才继续。重新开始会重新启用搜索。',wraplength=680).pack(anchor='w',pady=(0,12))
        widget=ttk.Button(footer,text='诊断运行 20 分钟（含搜索，不互动）',command=lambda:self.start(diagnostic=True))
        widget.pack(side='left');self.settings_widgets.append(widget)
        self.diagnostic_button=widget
        widget=ttk.Button(footer,text='保存搜索浏览设置',command=self.save_settings)
        widget.pack(side='right');self.settings_widgets.append(widget)

    def log_ui(self,event,**details):
        try:
            folder=Path(self.__dict__.get('cfg',{}).get('runtime_dir',DATA));folder.mkdir(parents=True,exist_ok=True)
            with (folder/'ui-events.jsonl').open('a') as output:
                output.write(json.dumps({'time':datetime.now().astimezone().isoformat(timespec='milliseconds'),
                    'event':event,**details},ensure_ascii=False)+'\n')
        except OSError:pass

    def trace_pointer(self,event):
        # No input values, key presses, clipboard contents, or API keys are recorded.
        widget=event.widget
        known=widget in [self.start_button,self.stop_button,self.test_button,getattr(self,'diagnostic_button',None)]
        self.log_ui('pointer',kind=str(event.type),widget_class=widget.winfo_class(),
            button=widget.cget('text') if known else None,state=str(widget.cget('state')) if known else None,
            x=event.x,y=event.y)

    def open_devices(self):
        if self.worker and self.worker.is_alive():self.status.set('请先停止浏览任务，再配置设备。');return
        if self.device_window:
            self.device_window.window.lift();return
        from device_ui import DeviceWindow
        self.device_window=DeviceWindow(self)

    def update_signature(self):
        import device_manager as dm
        import connections
        udid=self.cfg.get('device_udid','')
        try:mode=connections.load(udid)['mode'] if udid else 'usb'
        except ValueError as exc:self.signature_status.set('连接配置异常：'+str(exc));return
        if mode=='remote':
            self.signature_status.set('远程已有 WDA · 签名与重新启动由远端维护')
            return
        record=dm.signing_record(self.cfg.get('device_udid','')) if FROZEN or self.cfg.get('managed_device') else None
        state,remaining=dm.expiry_state(record,self.cfg.get('signature_warn_hours',48))
        if not FROZEN and not self.cfg.get('managed_device'):
            self.signature_status.set('开发目录模式；完整安装包提供设备与签名管理');return
        if state=='unknown':self.signature_status.set('请先完成设备配置，读取真实签名有效期');return
        date=__import__('datetime').datetime.fromisoformat(record['expires_at']).astimezone().strftime('%Y-%m-%d %H:%M')
        self.signature_status.set(('签名已到期，请重新签名' if state=='expired' else '签名即将到期，请续签' if state=='soon' else '签名有效')+' · '+date)
        notice=(record['udid'],record['expires_at'],state)
        if state in ('soon','expired') and notice not in self.signature_notices:
            self.signature_notices.add(notice)
            self.status.set('控制组件签名'+('已到期' if state=='expired' else '即将到期')+'，请点击“设备配置 / 重新签名”。')
            messagebox.showwarning('控制组件签名提醒',f'当前设备签名到期时间：{date}\n请在设备配置中点击“安装 / 重新签名控制组件”。',parent=self.root)

    def signature_tick(self):
        if not self.closing:self.update_signature();self.root.after(60000,self.signature_tick)

    def build_motion_settings(self):
        page=ttk.Frame(self.tabs)
        self.tabs.add(page,text='手势连接')
        frame=ui.scroll_form(page)
        w=ttk.Button(frame,text='保存设置',command=self.save_settings)
        w.pack(side='bottom',anchor='e',pady=(8,0));self.settings_widgets.append(w)
        ttk.Label(frame,text='在视频中央竖向滑动，避开底部导航与右侧按钮。',wraplength=670).pack(anchor='w')
        ttk.Label(frame,text='坐标和距离按屏幕百分比填写；用时越短，滑动越快。',wraplength=670).pack(anchor='w',pady=(4,10))
        self.motion_numbers={}
        for label,keys,limits,unit in [
            ('横向安全区',('gesture_left','gesture_right'),(20,65),'% 屏幕宽度'),
            ('纵向安全区',('gesture_top','gesture_bottom'),(22,70),'% 屏幕高度'),
            ('滑动距离',('gesture_distance_min','gesture_distance_max'),(15,40),'% 屏幕高度'),
            ('滑动用时',('gesture_duration_min','gesture_duration_max'),(180,800),'毫秒')]:
            row=ttk.Frame(frame);row.pack(fill='x',pady=5)
            ttk.Label(row,text=label,width=13).pack(side='left')
            for i,key in enumerate(keys):
                if i:ttk.Label(row,text='至').pack(side='left',padx=8)
                var=tk.StringVar(value=str(self.cfg[key]));self.motion_numbers[key]=var
                w=ttk.Spinbox(row,from_=limits[0],to=limits[1],textvariable=var,width=6)
                w.pack(side='left');self.settings_widgets.append(w)
            ttk.Label(row,text=unit).pack(side='left',padx=12)
        ttk.Label(frame,text='滑动前后检查页面，连续三次未切换才尝试恢复。',wraplength=670).pack(anchor='w',pady=(10,12))
        self.reconnect_enabled=tk.BooleanVar(value=self.cfg['reconnect_enabled'])
        w=ttk.Checkbutton(frame,text='控制连接中断后自动重连',variable=self.reconnect_enabled)
        w.pack(anchor='w');self.settings_widgets.append(w)
        self.close_business_chat=tk.BooleanVar(value=self.cfg['close_business_chat'])
        w=ttk.Checkbutton(frame,text='自动关闭已识别的广告咨询面板（不发送）',variable=self.close_business_chat)
        w.pack(anchor='w');self.settings_widgets.append(w)
        row=ttk.Frame(frame);row.pack(fill='x',pady=10)
        ttk.Label(row,text='5 分钟内重连上限').pack(side='left')
        self.reconnect_limit=tk.StringVar(value=str(self.cfg['reconnect_limit']))
        w=ttk.Spinbox(row,from_=1,to=3,textvariable=self.reconnect_limit,width=4)
        w.pack(side='left',padx=12);self.settings_widgets.append(w)
        ttk.Label(row,text='USB 等待 / 秒').pack(side='left')
        self.usb_wait=tk.StringVar(value=str(self.cfg['usb_wait_seconds']))
        w=ttk.Spinbox(row,from_=15,to=180,textvariable=self.usb_wait,width=5)
        w.pack(side='left',padx=12);self.settings_widgets.append(w)
        ttk.Label(frame,text='只重试页面读取，不重发点赞、收藏或评论。互动结果不明时停止。\n重连可能需要一些时间；手机应保持连接、解锁。',wraplength=670).pack(anchor='w',pady=(2,8))

    def build_ai_settings(self):
        page=ttk.Frame(self.tabs)
        self.tabs.insert(1,page,text='AI 评论')
        frame=ui.scroll_form(page)
        result_frame=ttk.Frame(self.tabs,padding=14)
        self.tabs.insert(2,result_frame,text='分析结果')
        self.provider_hint=tk.StringVar()
        ttk.Label(frame,textvariable=self.provider_hint,wraplength=690).pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(9,6))
        ttk.Label(row,text='API Key').pack(side='left')
        self.model_key=tk.StringVar()
        self.session_provider_keys={}
        self.current_provider=self.cfg['ai_provider']
        w=ttk.Entry(row,textvariable=self.model_key,show='•',width=36);w.pack(side='left',padx=8);self.settings_widgets.append(w)
        ttk.Label(row,text='仅本次使用，不写入配置').pack(side='left')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(0,8))
        ttk.Label(row,text='服务商').pack(side='left')
        self.ai_provider=tk.StringVar(value=PROVIDERS[self.cfg['ai_provider']]['label'])
        w=ttk.Combobox(row,textvariable=self.ai_provider,values=['OpenAI','DeepSeek'],state='readonly',width=10)
        w.pack(side='left',padx=(8,14));self.settings_widgets.append(w)
        ttk.Label(row,text='视觉模型').pack(side='left')
        self.ai_model=tk.StringVar(value=self.cfg['ai_model'])
        w=ttk.Entry(row,textvariable=self.ai_model,width=23);w.pack(side='left',padx=8);self.settings_widgets.append(w)
        self.provider_hint.set('采样画面和说明将发送至 '+self.ai_provider.get()+' API，调用会产生 API 费用。')
        self.ai_provider.trace_add('write',self.provider_changed)
        self.ai_mode=tk.StringVar(value={'ai':'AI 生成','rules':'固定规则'}.get(self.cfg['comments_mode'],'AI 生成'))
        self.ai_send=tk.StringVar(value='自动发送' if self.cfg['ai_send_mode']=='auto' else '只生成预览')
        row=ttk.Frame(frame);row.pack(fill='x',pady=(0,8))
        for label,var,values in [('评论方式',self.ai_mode,['AI 生成','固定规则']),('发送方式',self.ai_send,['自动发送','只生成预览'])]:
            ttk.Label(row,text=label).pack(side='left')
            w=ttk.Combobox(row,textvariable=var,values=values,width=13,state='readonly');w.pack(side='left',padx=(7,16));self.settings_widgets.append(w)
        self.ai_numbers={}
        fields=[('ai_confidence','保守度阈值',.5,1,.01),('ai_frames','采样张数',2,8,1),('ai_frame_interval','间隔 / 秒',1,10,.5),
                ('ai_max_calls_per_day','调用 / 天',1,100,1),('ai_max_words','英文词数上限',6,60,1),('comment_probability','尝试概率',0,1,.01)]
        grid=ttk.Frame(frame);grid.pack(fill='x',pady=(0,6))
        for i,(key,label,low,high,step) in enumerate(fields):
            r,c=divmod(i,3)
            ttk.Label(grid,text=label).grid(row=r*2,column=c,sticky='w',padx=(0,20))
            v=tk.StringVar(value=str(self.cfg[key]));self.ai_numbers[key]=v
            w=ttk.Spinbox(grid,from_=low,to=high,increment=step,textvariable=v,width=10)
            w.grid(row=r*2+1,column=c,sticky='w',padx=(0,30),pady=(2,6));self.settings_widgets.append(w)
        ttk.Label(frame,text='阈值是模型自评门槛，不代表准确率；部分画面不足以判断时跳过。',wraplength=690).pack(anchor='w',pady=(0,6))
        row=ttk.Frame(frame);row.pack(fill='x')
        self.ai_questions=tk.BooleanVar(value=self.cfg['ai_allow_questions'])
        self.ai_emoji=tk.BooleanVar(value=self.cfg['ai_allow_emoji'])
        for label,var in [('允许友好提问',self.ai_questions),('允许表情',self.ai_emoji)]:
            w=ttk.Checkbutton(row,text=label,variable=var);w.pack(side='left',padx=(0,20));self.settings_widgets.append(w)
        ttk.Label(frame,text='英文评论风格和额外要求').pack(anchor='w',pady=(7,3))
        self.ai_style=tk.Text(frame,height=3,wrap='word',font=('PingFang SC',11))
        self.ai_style.pack(fill='x');self.ai_style.insert('1.0',self.cfg['ai_style'])
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=(8,0))
        self.preview_button=ttk.Button(actions,text='理解当前视频并预览（不发送）',command=lambda:self.start(preview=True))
        self.preview_button.pack(side='left');self.settings_widgets.append(self.preview_button)
        w=ttk.Button(actions,text='保存 AI 设置',command=self.save_settings);w.pack(side='right');self.settings_widgets.append(w)
        ttk.Label(result_frame,text='显示最近一次模型判断。预览不会发评论，也计入模型调用上限。',wraplength=690).pack(anchor='w',pady=(0,8))
        result_body=ttk.Frame(result_frame);result_body.pack(fill='both',expand=True)
        self.ai_result=tk.Text(result_body,height=15,wrap='word',font=('PingFang SC',12),state='disabled')
        result_scroll=ttk.Scrollbar(result_body,orient='vertical',command=self.ai_result.yview)
        result_scroll.pack(side='right',fill='y');self.ai_result.configure(yscrollcommand=result_scroll.set)
        self.ai_result.pack(side='left',fill='both',expand=True)
        self.ai_result_tab=result_frame

    def provider_changed(self,*_):
        new=self.ai_provider.get().lower()
        if new not in PROVIDERS:return
        self.session_provider_keys[self.current_provider]=self.model_key.get()
        self.model_key.set(self.session_provider_keys.get(new,''))
        self.current_provider=new
        self.ai_model.set(PROVIDERS[new]['model'])
        self.provider_hint.set('采样画面和说明将发送至 '+PROVIDERS[new]['label']+' API，调用会产生 API 费用。')

    def refresh_rules(self,index=None):
        self.rule_list.delete(0,'end')
        for rule in self.comment_rules: self.rule_list.insert('end',rule.get('name','未命名规则'))
        if index is not None and self.comment_rules:
            self.rule_list.selection_set(index)
            self.load_rule(index)
        else:
            self.selected_rule=None
            self.rule_name.set('')
            self.rule_terms.set('')
            self.rules.delete('1.0','end')

    def load_rule(self,index):
        self.selected_rule=index
        r=self.comment_rules[index]
        self.rule_name.set(r.get('name',''))
        self.rule_terms.set('，'.join(r['match_any']))
        self.rules.delete('1.0','end'); self.rules.insert('1.0','\n'.join(r['replies']))

    def store_current_rule(self):
        if self.selected_rule is not None:
            self.comment_rules[self.selected_rule]={'name':self.rule_name.get().strip() or '未命名规则',
                'match_any':self.split(self.rule_terms.get()),
                'replies':[s.strip() for s in self.rules.get('1.0','end').splitlines() if s.strip()]}

    def select_rule(self,_=None):
        selected=self.rule_list.curselection()
        if selected:
            cfg['like_interval_min']=int(self.like_interval_min.get())
            cfg['like_interval_max']=int(self.like_interval_max.get())
            self.store_current_rule()
            self.load_rule(selected[0])

    def new_rule(self):
        self.store_current_rule()
        self.comment_rules.append({'name':'新规则','match_any':[],'replies':[]})
        self.refresh_rules(len(self.comment_rules)-1)

    def delete_rule(self):
        if self.selected_rule is not None:
            del self.comment_rules[self.selected_rule]
            self.refresh_rules(0 if self.comment_rules else None)

    def split(self,text):
        return list(dict.fromkeys(s.strip() for s in text.replace('，',',').split(',') if s.strip()))

    def save_settings(self,quiet=False):
        self.cfg['diagnostic_screenshots']=self.diagnostic_images.get()
        try:
            minutes=int(self.minutes.get())
            if not 1<=minutes<=120:
                raise ValueError('时长须为 1 到 120 分钟')
            cfg=dict(self.cfg)
            cfg.update(minutes=minutes,keywords=self.split(self.keywords.get()),relevance_terms=self.split(self.terms.get()),exclude_keywords=self.split(self.excludes.get()))
            if any(not english_text(term) for term in cfg['keywords']):
                raise ValueError('搜索关键词请全部使用英文')
            for key,var in self.flags.items(): cfg[key]=var.get()
            for key,var in self.limits.items():
                value=int(var.get())
                if not 0<=value<=50: raise ValueError('每日次数须为 0 到 50')
                cfg['max_'+key+'_per_day']=value
            self.store_current_rule()
            rules=self.comment_rules
            if not isinstance(rules,list): raise ValueError('评论规则必须是 JSON 数组')
            for rule in rules:
                if not isinstance(rule,dict) or not isinstance(rule.get('match_any'),list) or not isinstance(rule.get('replies'),list):
                    raise ValueError('每条规则必须包含 match_any 和 replies 数组')
                if not rule['match_any'] or not rule['replies']:
                    raise ValueError('触发词和评论内容不能为空')
                if any(not isinstance(s,str) or not s.strip() for s in rule['match_any']+rule['replies']):
                    raise ValueError('触发词和评论必须是非空文本')
                if any(not english_text(reply) for reply in rule['replies']):
                    raise ValueError('评论内容请使用英文')
                if any(len(reply)>500 for reply in rule['replies']): raise ValueError('单条评论请控制在 500 字符以内')
            if not cfg['relevance_terms'] and not cfg['keywords']: raise ValueError('请至少填写一个搜索关键词或内容匹配词')
            cfg['comment_rules']=rules
            cfg['comments_mode']='ai' if self.ai_mode.get()=='AI 生成' else 'rules'
            cfg['ai_send_mode']='auto' if self.ai_send.get()=='自动发送' else 'preview'
            cfg['ai_model']=self.ai_model.get().strip()
            cfg['ai_provider']=self.ai_provider.get().lower()
            if cfg['ai_provider']=='deepseek' and cfg['ai_model'] not in ['deepseek-flash','deepseek-v4-flash-vision-exp']:
                raise ValueError('DeepSeek 图片理解请使用 deepseek-flash')
            if not cfg['ai_model']: raise ValueError('请填写视觉模型名称')
            bounds={'ai_confidence':(.5,1,float),'ai_frames':(2,8,int),'ai_frame_interval':(1,10,float),
                    'ai_max_calls_per_day':(1,100,int),'ai_max_words':(6,60,int),'comment_probability':(0,1,float)}
            for key,(low,high,convert) in bounds.items():
                value=convert(self.ai_numbers[key].get())
                if not low<=value<=high: raise ValueError(f'{key} 超出允许范围 {low}～{high}')
                cfg[key]=value
            cfg['ai_allow_questions']=self.ai_questions.get()
            cfg['ai_allow_emoji']=self.ai_emoji.get()
            cfg['ai_style']=self.ai_style.get('1.0','end').strip()
            if not cfg['ai_style'] or len(cfg['ai_style'])>3000: raise ValueError('评论要求须为 1～3000 字符')
            cfg['recovery_enabled']=self.recovery_enabled.get()
            cfg['recovery_attempts']=int(self.recovery_attempts.get())
            cfg['recovery_run_limit']=int(self.recovery_limit.get())
            if not 1<=cfg['recovery_attempts']<=5 or not 1<=cfg['recovery_run_limit']<=20:
                raise ValueError('恢复尝试须为 1～5，5 分钟内恢复上限须为 1～20')
            for key,var in self.motion_numbers.items():cfg[key]=int(var.get())
            cfg['reconnect_enabled']=self.reconnect_enabled.get()
            cfg['close_business_chat']=self.close_business_chat.get()
            cfg['reconnect_limit']=int(self.reconnect_limit.get())
            cfg['usb_wait_seconds']=int(self.usb_wait.get())
            for key,var in self.search_numbers.items():cfg[key]=int(var.get())
            if not (1<=cfg['search_videos_per_query']<=5 and 1<=cfg['search_result_scrolls']<=3
                and 5<=cfg['search_watch_min']<=cfg['search_watch_max']<=90):
                raise ValueError('搜索视频数须为 1～5，滚动次数为 1～3，观看时长为 5～90 秒且从小到大')
            validate_motion(cfg)
            from device_store import preserve_identity
            cfg=preserve_identity(cfg,self.__dict__.get('config_path',ROOT/'config.json'))
            atomic_json(self.__dict__.get('config_path',ROOT/'config.json'),cfg)
            self.cfg=cfg
            if not quiet: self.status.set('设置已保存')
            return True
        except (ValueError,TypeError,OSError) as exc:
            if self.__dict__.get('batch_mode'):self.status.set('配置有误：'+str(exc))
            else:messagebox.showerror('请检查配置',str(exc),parent=self.root)
            return False

    def set_running(self,running):
        hint=self.__dict__.get('edit_hint')
        if hint:hint.set('运行中，停止后可编辑设置   ·   Esc 停止' if running else '仅此设备   ·   ⌘ Return 开始   ·   Esc 停止')
        self.start_button.configure(state='disabled' if running else 'normal',text='开始浏览')
        self.test_button.configure(state='disabled' if running else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        for w in self.settings_widgets: w.configure(state='disabled' if running else 'normal')
        self.rules.configure(state='disabled' if running else 'normal')
        self.rule_list.configure(state='disabled' if running else 'normal')
        self.ai_style.configure(state='disabled' if running else 'normal')
        for w in self.settings_widgets:
            if isinstance(w,ttk.Combobox) and not running:w.configure(state='readonly')

    def start(self,test=False,preview=False,diagnostic=False):
        self.log_ui('start_requested',test=test,preview=preview,diagnostic=diagnostic,worker_alive=bool(self.worker and self.worker.is_alive()),stopping=self.stop_event.is_set(),generation=self.generation)
        if self.closing:return
        if self.worker and self.worker.is_alive():
            if self.stop_event.is_set():
                if self.pending_start is None:
                    self.pending_start=dict(test=test,preview=preview,diagnostic=diagnostic) if any((test,preview,diagnostic)) else dict(self.last_start)
                self.status.set('已安排重新启动，等待上次任务完全结束并释放设备…')
                self.start_button.configure(state='disabled',text='等待重新启动…')
            else:self.status.set('任务正在运行，请先停止；不会重复启动第二个任务。')
            return
        if self.run_ui_active:
            self.pending_start=dict(self.last_start) if self.stop_event.is_set() and not any((test,preview,diagnostic)) else dict(test=test,preview=preview,diagnostic=diagnostic)
            self.status.set('已安排重新启动，正在确认上次任务已退出…');return
        if self.device_window and self.device_window.busy:
            self.status.set('设备配置正在进行，请等待完成。');return
        if FROZEN or self.cfg.get('managed_device'):
            import device_manager as dm
            import connections
            try:external=connections.load(self.cfg.get('device_udid',''))['mode']=='remote' if self.cfg.get('device_udid') else False
            except ValueError as exc:self.status.set(str(exc));return
            blocker=None if external else dm.setup_blocker(self.cfg.get('device_udid',''))
            if blocker:
                self.status.set(blocker)
                if not self.__dict__.get('batch_mode'):self.open_devices()
                return
            record=dm.signing_record(self.cfg.get('device_udid',''))
            if not external and dm.expiry_state(record)[0] in ('unknown','expired'):
                self.status.set('请先完成设备配置或重新签名。')
                if not self.__dict__.get('batch_mode'):self.open_devices()
                return
        if not self.save_settings(quiet=True): return
        if preview and not (self.model_key.get().strip() or os.environ.get(PROVIDERS[self.cfg['ai_provider']]['env'])):
            self.status.set('请填写 '+PROVIDERS[self.cfg['ai_provider']]['label']+' API Key；密钥不会保存到配置')
            self.tabs.select(1)
            return
        self.stop_event=threading.Event()
        run_cfg=dict(self.cfg)
        if diagnostic:run_cfg.update(enable_likes=False,enable_saves=False,enable_comments=False,enable_search=True,max_swipes_per_run=1000)
        self.generation+=1
        generation=self.generation
        notify=lambda row:self.events.put({**row,'generation':generation})
        try:self.runner=Runner(run_cfg,stop=self.stop_event,notify=notify,seconds=1200 if diagnostic else (45 if test else None),browse_only=test,
                           model_api_key=self.model_key.get(),analysis_only=preview)
        except Exception as exc:
            self.status.set('无法开始：'+str(exc).splitlines()[0][:220]);self.append(str(exc));return
        self.last_start=dict(test=test,preview=preview,diagnostic=diagnostic)
        self.worker_result=self.finished_row=None
        self.run_error='任务未返回正常结束状态'
        self.stop_requested_at=None
        self.run_ui_active=True
        self.phase_text='正在连接手机…'
        self.phase_started=time.monotonic()
        self.set_running(True)
        self.status.set('正在连接手机…')
        def work():
            result='error'
            try:result=self.runner.run()
            except Exception as exc:
                traceback.print_exc()
                notify({'event':'error','message':'任务异常退出：'+str(exc).splitlines()[0][:220]})
            finally:notify({'event':'worker_result','status':result})
        self.worker=threading.Thread(target=work,daemon=True)
        try:self.worker.start()
        except Exception as exc:
            self.worker=None;self.run_ui_active=False;self.set_running(False)
            self.status.set('无法启动后台任务：'+str(exc).splitlines()[0][:220])

    def stop(self):
        self.log_ui('stop_requested',worker_alive=bool(self.worker and self.worker.is_alive()),generation=self.generation)
        if not self.worker or not self.worker.is_alive():
            self.status.set('当前没有运行中的任务。');return
        self.stop_event.set()
        if self.stop_requested_at is None:self.stop_requested_at=time.monotonic()
        self.stop_button.configure(state='disabled')
        self.start_button.configure(state='normal' if not self.closing else 'disabled',text='停止后重新开始')
        self.status.set('正在停止，等待当前手机命令结束；可点击“停止后重新开始”排队启动。')

    def pause_hosting(self):
        if self.worker and self.worker.is_alive() and not self.stop_event.is_set():
            self.runner.request_pause();self.status.set('正在暂停，等待当前动作完成；显示已暂停托管后再操作手机。')

    def resume_hosting(self):
        if self.worker and self.worker.is_alive() and not self.stop_event.is_set():self.runner.request_resume()

    def append(self,text):
        self.log_text.configure(state='normal')
        self.log_text.insert('end',text+'\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def poll(self):
        closed=False
        try:closed=self.process_events()
        except Exception:
            traceback.print_exc()
            self.status.set('界面更新异常，正在继续接收任务状态；请查看 console.log。')
        finally:
            if not closed:self.root.after(200,self.poll)

    def process_events(self):
        try:
            while True:
                row=self.events.get_nowait()
                if row.get('generation',self.generation)!=self.generation:continue
                if row['event']=='worker_result':
                    self.worker_result=row['status'];continue
                msg=row.get('message',row['event'])
                if row['event'] in ('error','feature_disabled','resume_blocked'):
                    from notifications import TaskAlerts
                    if not hasattr(self,'task_alerts'):
                        self.task_alerts=TaskAlerts(self.cfg['runtime_dir'],self.cfg.get('device_name','iPhone'))
                    if self.task_alerts.handle(self.generation,row):
                        try:self.root.bell()
                        except tk.TclError:pass
                self.append(row.get('time','')[-8:]+'  '+msg)
                if row['event']=='ai_preview':
                    self.ai_result.configure(state='normal');self.ai_result.insert('end','\n\n仅预览，未发送。');self.ai_result.configure(state='disabled')
                if row['event'] in ['ai_result','ai_skip']:
                    text=msg
                    if row.get('summary'):text+='\n\n内容摘要：\n'+row['summary']
                    if 'confidence' in row:text+='\n\n模型自评：'+str(row['confidence'])
                    if row.get('evidence'):text+='\n\n判断依据：\n'+'\n'.join(e['source']+': '+e['detail'] for e in row['evidence'])
                    if row.get('comment'):text+='\n\n英文评论：\n'+row['comment']
                    self.ai_result.configure(state='normal');self.ai_result.delete('1.0','end');self.ai_result.insert('1.0',text);self.ai_result.configure(state='disabled')
                    if row['event']=='ai_result':self.tabs.select(self.ai_result_tab)
                if row['event']=='error':
                    self.run_error=msg.splitlines()[0][:220];self.phase_text=''
                    self.status.set('已停止：'+self.run_error)
                elif row['event']=='finished':
                    self.finished_row=row
                    self.append('记录保存在：'+row['log_dir'])
                elif not self.stop_event.is_set():
                    if self.phase_text!=msg:self.phase_started=time.monotonic()
                    self.phase_text=msg;self.status.set(msg)
        except queue.Empty:
            pass
        if self.run_ui_active and self.worker_result is not None and not self.worker.is_alive():
            self.worker.join(timeout=0)
            self.run_ui_active=False
            self.set_running(False)
            if self.worker_result!='error':
                self.status.set('已停止，可重新开始' if self.worker_result=='stopped' else '本次运行已完成，可重新开始')
            else:self.status.set('已停止：'+self.run_error)
            restart=self.pending_start;self.pending_start=None
            if restart is not None and not self.closing:
                self.root.after(0,lambda options=restart:self.start(**options))
        elif self.run_ui_active and self.stop_event.is_set():
            elapsed=int(time.monotonic()-(self.stop_requested_at or time.monotonic()))
            self.status.set(f'正在停止（已等待 {elapsed} 秒）'+('，完成后自动重新启动…' if self.pending_start is not None else '，等待当前手机命令结束…'))
        elif self.run_ui_active and self.phase_text.startswith('正在'):
            self.status.set(self.phase_text+f'（已等待 {int(time.monotonic()-self.phase_started)} 秒）')
        active=bool(self.worker and self.worker.is_alive() and not self.stop_event.is_set())
        pause_state=getattr(getattr(self,'runner',None),'pause_state','running')
        self.pause_button.configure(state='normal' if active and pause_state=='running' else 'disabled')
        self.resume_button.configure(state='normal' if active and pause_state=='paused' else 'disabled')
        if hasattr(self,'runner'):
            feature=self.runner.feature_state['search']
            self.feature_status.set('搜索状态：'+('本轮启用' if feature['enabled'] else (feature['reason'] or '本轮未启用')))
            c=self.runner.counts
            self.stats.set(f"浏览 {c['watched']} · 翻页 {self.runner.swipes} · 点赞 {c['likes']} · 收藏 {c['saves']} · 评论 {c['comments']} · 搜索 {self.runner.searches} · 恢复 {self.runner.recoveries} · 重连 {self.runner.reconnections}")
        if self.closing and (not self.worker or not self.worker.is_alive()):
            self.root.destroy(); return True

    def export_diagnostics(self):
        if self.worker and self.worker.is_alive():
            self.status.set('请先停止当前任务，再导出完整诊断包。');return
        path=filedialog.asksaveasfilename(parent=self.root,title='导出最近一轮诊断',
            defaultextension='.zip',initialfile='Instagram-diagnostics-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip',
            filetypes=[('ZIP 诊断包','*.zip')])
        if not path:return
        try:
            result=diagnostics.export(self.cfg['runtime_dir'],path,self.include_diagnostic_images.get())
            self.status.set('诊断包已导出：'+Path(path).name)
            self.append('诊断包包含 '+str(result['cases'])+' 个异常案例；配置、密钥和原始日志未导出。')
        except Exception as exc:messagebox.showerror('导出失败',str(exc),parent=self.root)

    def open_logs(self):
        folder=Path(self.cfg['runtime_dir'])/'runs'; folder.mkdir(exist_ok=True)
        subprocess.Popen(['/usr/bin/open',str(folder)])

    def close(self):
        if self.device_window and self.device_window.busy:
            self.status.set('设备配置仍在进行，请等待完成后退出。');return
        self.closing=True
        self.pending_start=None
        if self.worker and self.worker.is_alive(): self.stop()
        else: self.root.destroy()

if __name__=='__main__':
    root=tk.Tk()
    Panel(root)
    root.mainloop()
