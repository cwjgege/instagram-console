"""Fixed-location controls for exactly one selected physical iPhone."""
import tkinter as tk
import queue,threading
from tkinter import ttk
import ui_theme as ui
from location_manager import NEW_YORK,validate_target

class LocationWindow:
    def __init__(self,owner,cfg):
        self.owner=owner;self.manager=owner.locations;self.udid=cfg['device_udid'];self.closed=False;self.map_window=None;self.permission_busy=False;self.permission_message='';self.permission_events=queue.Queue()
        self.window=tk.Toplevel(owner.root);w=self.window
        w.title('固定定位 · '+cfg.get('device_name','iPhone'));w.geometry(f'620x{min(720,w.winfo_screenheight()-90)}');w.minsize(520,500)
        w.protocol('WM_DELETE_WINDOW',self.close)
        footer=ttk.Frame(w,padding=16);footer.pack(side='bottom',fill='x')
        ui.version_label(footer)
        buttons=ttk.Frame(footer);buttons.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Button(buttons,text='关闭',command=self.close).pack(side='right')
        w.bind('<Command-f>',lambda _:self.open_map())
        body=ui.scroll_form(w,padding=22)
        ttk.Label(body,text='固定定位',font=('PingFang SC',22,'bold')).pack(anchor='w')
        self.identity_text=tk.StringVar()
        self.update_identity(cfg)
        ttk.Label(body,textvariable=self.identity_text,style='Muted.TLabel').pack(anchor='w',pady=(6,18))
        ttk.Label(body,text='通过 USB 或已配对的无线连接设置此手机的位置。首次使用请先完成电脑信任、开发者模式和 Xcode 设备准备。',wraplength=560).pack(fill='x',pady=(0,18))
        saved=self.manager.snapshot(self.udid)
        self.lat=tk.StringVar(value=str(saved.get('latitude') if saved.get('latitude') is not None else ''))
        self.lon=tk.StringVar(value=str(saved.get('longitude') if saved.get('longitude') is not None else ''))
        self.entries=[]
        for label,var in [('纬度（-90～90）',self.lat),('经度（-180～180）',self.lon)]:
            ttk.Label(body,text=label).pack(anchor='w',pady=(0,4))
            entry=ttk.Entry(body,textvariable=var);entry.pack(fill='x',pady=(0,12));self.entries.append(entry)
        pick=ttk.Frame(body);pick.pack(fill='x',pady=(0,18))
        self.preset=ttk.Button(pick,text='填入纽约',command=self.new_york);self.preset.pack(side='left')
        self.map_button=ttk.Button(pick,text='地图搜索与选点…',command=self.open_map);self.map_button.pack(side='left',padx=10)
        self.keep=tk.BooleanVar(value=bool(saved.get('guard_enabled')))
        def keep_changed():
            if not self.keep.get():self.manager.stop_guard(self.udid)
        self.keep_check=ttk.Checkbutton(body,text='设置后自动保持位置（每 10 秒检查）',variable=self.keep,command=keep_changed)
        self.keep_check.pack(anchor='w',pady=(0,10))
        ttk.Label(body,text='偏差超过 100 米且超出精度误差时纠正。需更新手机控件并允许“始终”访问精确位置；旧数据、精度不足或断线时不自动改位置。',style='Muted.TLabel',wraplength=560).pack(fill='x',pady=(0,14))
        self.permission_button=ttk.Button(body,text='首次授权定位读取…',command=self.request_permission);self.permission_button.pack(anchor='w',pady=(0,14))
        actions=ttk.Frame(body);actions.pack(fill='x')
        self.apply_button=ttk.Button(actions,text='设置定位',style='Primary.TButton',command=lambda:self.request(False));self.apply_button.pack(side='left')
        self.clear_button=ttk.Button(actions,text='恢复真实定位',command=lambda:self.request(True));self.clear_button.pack(side='left',padx=10)
        self.progress=ttk.Progressbar(footer,mode='indeterminate');self.progress.pack(fill='x',pady=(0,8))
        self.status=tk.StringVar();self.label=ttk.Label(footer,textvariable=self.status,wraplength=560);self.label.pack(fill='x')
        self.guard_status=tk.StringVar();self.guard_label=ttk.Label(footer,textvariable=self.guard_status,wraplength=560);self.guard_label.pack(fill='x',pady=(6,0))
        ttk.Label(body,text='关闭此窗口会保留定位连接；退出控制台会尝试恢复真实定位。设置和恢复后请在手机地图中确认，自动保持会读取系统报告的位置，并不保证其他 App 已刷新位置。本版不包含轨迹。',style='Muted.TLabel',wraplength=560).pack(fill='x')
        try:validate_target(self.udid);self.target_error=''
        except ValueError as exc:self.target_error=str(exc)
        self.error='';self.was_busy=False;self.poll()
    def update_identity(self,cfg):
        name=cfg.get('device_alias') or cfg.get('device_name','iPhone')
        self.window.title(ui.version_title('固定定位 · '+name))
        self.identity_text.set(name+'  ·  iOS '+cfg.get('device_ios','未知')+'  ·  编号末尾 '+self.udid[-8:])

    def open_map(self):
        if self.map_window and not self.map_window.closed:self.map_window.window.lift();return
        from location_map import MapWindow
        def selected(lat,lon):
            self.lat.set(f'{lat:.6f}');self.lon.set(f'{lon:.6f}');self.error=''
        self.map_window=MapWindow(self.window,self.lat.get(),self.lon.get(),selected)
    def new_york(self):self.lat.set(str(NEW_YORK[0]));self.lon.set(str(NEW_YORK[1]))
    def request_permission(self):
        if self.permission_busy or self.owner.closing:return
        panel=self.owner.panels.get(self.udid) if hasattr(self.owner,'panels') else None
        if panel and panel.worker and panel.worker.is_alive():self.error='请先停止此设备的浏览任务，再请求定位授权。';self.refresh();return
        self.permission_busy=True;self.error='';self.permission_message='正在打开手机控制组件并请求定位授权…';self.refresh()
        def work():
            try:
                from location_readback import LocationReader
                reader=LocationReader();value=reader.request_permission(self.udid)
                message='定位授权已就绪，可勾选自动保持并设置坐标。' if value.get('authorizationStatus')==3 and value.get('accuracyAuthorization')==0 else '请在手机允许使用 App 时访问位置，再到定位服务将控件设为“始终”，并开启精确位置。'
                if value.get('authorizationStatus')==3 and self.udid not in self.manager.guard.sessions:reader.stop(self.udid)
                self.permission_events.put((message,False))
            except Exception as exc:self.permission_events.put(('定位授权请求未完成：'+str(exc)[:300],True))
        threading.Thread(target=work,daemon=True).start()
    def request(self,clear):
        self.error='';self.permission_message=''
        try:
            if clear:self.keep.set(False);self.manager.clear(self.udid)
            else:self.manager.apply(self.udid,self.lat.get(),self.lon.get(),keep=self.keep.get())
        except Exception as exc:self.error=str(exc)
        self.refresh()
    def refresh(self):
        state=self.manager.snapshot(self.udid);busy=state['phase']=='busy';disabled=busy or self.permission_busy or bool(self.target_error) or self.owner.closing
        for widget in (*self.entries,self.preset,self.map_button,self.apply_button,self.clear_button):widget.configure(state='disabled' if disabled else 'normal')
        if busy and not self.was_busy:self.progress.start(12)
        elif not busy and self.was_busy:self.progress.stop()
        self.was_busy=busy
        self.permission_button.configure(state='disabled' if disabled or self.permission_busy else 'normal')
        self.guard_status.set(state.get('guard_message','未开启自动保持；勾选后点击“设置定位”启用。'))
        self.guard_label.configure(foreground=ui.GREEN if state.get('guard_status')=='confirmed' else ui.RED if state.get('guard_status') in ('unconfirmed','paused') else ui.MUTED)
        self.status.set(self.target_error or self.error or self.permission_message or state['message'])
        self.label.configure(foreground=ui.RED if self.target_error or self.error or state['phase']=='unknown' else ui.GREEN if state['phase']=='applied' else ui.INK)
    def poll(self):
        if self.closed:return
        try:
            while True:
                message,failed=self.permission_events.get_nowait();self.permission_busy=False
                self.error=message if failed else '';self.permission_message='' if failed else message
        except queue.Empty:pass
        self.refresh();self.window.after(300,self.poll)
    def close(self):
        self.closed=True
        if self.map_window and not self.map_window.closed:self.map_window.close()
        self.owner.location_windows.pop(self.udid,None);self.window.destroy()
