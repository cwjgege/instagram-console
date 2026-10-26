"""Fixed-location controls for exactly one selected physical iPhone."""
import tkinter as tk
from tkinter import ttk
import ui_theme as ui
from location_manager import NEW_YORK,validate_target

class LocationWindow:
    def __init__(self,owner,cfg):
        self.owner=owner;self.manager=owner.locations;self.udid=cfg['device_udid'];self.closed=False;self.map_window=None
        self.window=tk.Toplevel(owner.root);w=self.window
        w.title('固定定位 · '+cfg.get('device_name','iPhone'));w.geometry(f'620x{min(720,w.winfo_screenheight()-90)}');w.minsize(520,500)
        w.protocol('WM_DELETE_WINDOW',self.close)
        footer=ttk.Frame(w,padding=16);footer.pack(side='bottom',fill='x')
        buttons=ttk.Frame(footer);buttons.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Button(buttons,text='关闭',command=self.close).pack(side='right')
        w.bind('<Command-f>',lambda _:self.open_map())
        body=ui.scroll_form(w,padding=22)
        ttk.Label(body,text='固定定位',font=('PingFang SC',22,'bold')).pack(anchor='w')
        ttk.Label(body,text=cfg.get('device_name','iPhone')+'  ·  iOS '+cfg.get('device_ios','未知')+'  ·  编号末尾 '+self.udid[-8:],style='Muted.TLabel').pack(anchor='w',pady=(6,18))
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
        actions=ttk.Frame(body);actions.pack(fill='x')
        self.apply_button=ttk.Button(actions,text='设置定位',style='Primary.TButton',command=lambda:self.request(False));self.apply_button.pack(side='left')
        self.clear_button=ttk.Button(actions,text='恢复真实定位',command=lambda:self.request(True));self.clear_button.pack(side='left',padx=10)
        self.progress=ttk.Progressbar(footer,mode='indeterminate');self.progress.pack(fill='x',pady=(0,8))
        self.status=tk.StringVar();self.label=ttk.Label(footer,textvariable=self.status,wraplength=560);self.label.pack(fill='x')
        ttk.Label(body,text='关闭此窗口会保留定位连接；退出控制台会尝试恢复真实定位。设置和恢复后请在手机地图中确认，界面不读取 GPS 实际位置。本版不包含轨迹。',style='Muted.TLabel',wraplength=560).pack(fill='x')
        try:validate_target(self.udid);self.target_error=''
        except ValueError as exc:self.target_error=str(exc)
        self.error='';self.was_busy=False;self.poll()
    def open_map(self):
        if self.map_window and not self.map_window.closed:self.map_window.window.lift();return
        from location_map import MapWindow
        def selected(lat,lon):
            self.lat.set(f'{lat:.6f}');self.lon.set(f'{lon:.6f}');self.error=''
        self.map_window=MapWindow(self.window,self.lat.get(),self.lon.get(),selected)
    def new_york(self):self.lat.set(str(NEW_YORK[0]));self.lon.set(str(NEW_YORK[1]))
    def request(self,clear):
        self.error=''
        try:
            if clear:self.manager.clear(self.udid)
            else:self.manager.apply(self.udid,self.lat.get(),self.lon.get())
        except Exception as exc:self.error=str(exc)
        self.refresh()
    def refresh(self):
        state=self.manager.snapshot(self.udid);busy=state['phase']=='busy';disabled=busy or bool(self.target_error) or self.owner.closing
        for widget in (*self.entries,self.preset,self.map_button,self.apply_button,self.clear_button):widget.configure(state='disabled' if disabled else 'normal')
        if busy and not self.was_busy:self.progress.start(12)
        elif not busy and self.was_busy:self.progress.stop()
        self.was_busy=busy
        self.status.set(self.target_error or self.error or state['message'])
        self.label.configure(foreground=ui.RED if self.target_error or self.error or state['phase']=='unknown' else ui.GREEN if state['phase']=='applied' else ui.INK)
    def poll(self):
        if self.closed:return
        self.refresh();self.window.after(300,self.poll)
    def close(self):
        self.closed=True
        if self.map_window and not self.map_window.closed:self.map_window.close()
        self.owner.location_windows.pop(self.udid,None);self.window.destroy()
