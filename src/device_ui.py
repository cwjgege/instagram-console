"""Nonblocking first-run guide and device/signature management window."""
import json
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk,messagebox
from pathlib import Path
from app_paths import DATA,FROZEN
import wireless_setup as ws
import device_manager as dm
import ui_theme as ui

class DeviceWindow:
    def __init__(self,panel):
        self.panel=panel;self.events=queue.Queue();self.busy=False;self.devices=[];self.team_map={};self.awaiting_wireless=None;self.wireless_stop=threading.Event();self.wireless_running=False
        self.window=tk.Toplevel(panel.root);self.window.title('设备配置与签名')
        self.window.geometry(f'820x{min(800,max(560,self.window.winfo_screenheight()-100))}')
        self.window.minsize(620,540)
        # Reserve results and actions before allocating the scrollable form.
        footer=ttk.Frame(self.window,padding=(18,12));footer.pack(side='bottom',fill='x')
        self.status=tk.StringVar(value='准备就绪。')
        self.status_label=ttk.Label(footer,textvariable=self.status,wraplength=760)
        self.status_label.pack(fill='x',pady=(0,8))
        self.progress=ttk.Progressbar(footer,mode='indeterminate');self.progress.pack(fill='x',pady=(0,10))
        area=ttk.Frame(self.window);area.pack(fill='both',expand=True)
        self.canvas=tk.Canvas(area,highlightthickness=0,borderwidth=0)
        scrollbar=ttk.Scrollbar(area,orient='vertical',command=self.canvas.yview)
        scrollbar.pack(side='right',fill='y');self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        body=ttk.Frame(self.canvas,padding=22)
        self.form_id=self.canvas.create_window((0,0),window=body,anchor='nw')
        body.bind('<Configure>',lambda event:self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',lambda event:self.resize_form(event,body))
        ui.bind_form_scroll(self.window,self.canvas,body)
        ttk.Label(body,text='连接 iPhone，完成首次配置',font=('PingFang SC',20,'bold')).pack(anchor='w')
        ttk.Label(body,text='1  检查 Xcode  →  2  选择 USB 设备  →  3  签名安装  →  4  拔线验证 Wi-Fi',wraplength=720).pack(anchor='w',pady=(8,16))
        self.environment=tk.StringVar(value='正在检测环境…')
        ttk.Label(body,textvariable=self.environment,wraplength=720).pack(anchor='w')
        row=ttk.Frame(body);row.pack(fill='x',pady=(8,16))
        ttk.Button(row,text='重新检测连接',command=self.scan).pack(side='left')
        ttk.Button(row,text='打开 Xcode',command=self.open_xcode).pack(side='left',padx=10)
        ttk.Button(row,text='打开诊断日志',command=self.open_logs).pack(side='left')
        ttk.Label(body,text='仅列出通过 USB 连接的 iPhone').pack(anchor='w')
        self.device=tk.StringVar();self.device_box=ttk.Combobox(body,textvariable=self.device,state='readonly')
        self.device_box.pack(fill='x',pady=(5,8));self.device_box.bind('<<ComboboxSelected>>',lambda _:self.show_device())
        self.detail=tk.StringVar(value='请连接手机、解锁，并在手机上选择“信任此电脑”。')
        ttk.Label(body,textvariable=self.detail,wraplength=710).pack(anchor='w')
        ttk.Label(body,text='“信任此电脑”是 USB 配对；“VPN 与设备管理”是开发者 App 验证。没有对应条目不一定异常，是否就绪以组件实际安装和连接验证为准。',style='Muted.TLabel',wraplength=710).pack(anchor='w',pady=(8,0))
        ttk.Label(body,text='签名团队（与手机 Apple ID 无需相同）').pack(anchor='w',pady=(16,5))
        self.team=tk.StringVar();self.team_box=ttk.Combobox(body,textvariable=self.team)
        self.team_box.pack(fill='x')
        ttk.Label(body,text='检测不到团队时：在 Xcode → Settings → Accounts 登录开发账号，\n完成证书配置后重新检测；也可输入该账号的 10 位 Team ID。',wraplength=710).pack(anchor='w',pady=(6,12))
        self.auto_wireless=tk.BooleanVar(value=True)
        self.wireless_check=ttk.Checkbutton(body,text='组件就绪后自动准备无线连接，拔线后验证并切换 Wi-Fi',variable=self.auto_wireless)
        self.wireless_check.pack(anchor='w',pady=(4,6))
        self.wireless_status=tk.StringVar(value='首次仍需 USB 信任与开发者模式；无线开关由软件配置，无需另去 Finder。')
        ttk.Label(body,textvariable=self.wireless_status,wraplength=710,style='Muted.TLabel').pack(fill='x')
        self.wireless_button=ttk.Button(body,text='准备无线连接（无需重签名）',command=self.prepare_wireless)
        self.wireless_button.pack(anchor='w',pady=(6,12))
        self.signature=tk.StringVar(value='尚未读取签名。')
        ttk.Label(body,textvariable=self.signature,wraplength=710).pack(anchor='w')
        row=ttk.Frame(body);row.pack(fill='x',pady=(10,10))
        ttk.Label(row,text='到期前提醒 / 小时').pack(side='left')
        self.hours=tk.StringVar(value=str(panel.cfg.get('signature_warn_hours',48)))
        ttk.Spinbox(row,from_=1,to=168,textvariable=self.hours,width=6).pack(side='left',padx=10)
        ttk.Button(row,text='保存提醒设置',command=self.save_reminder).pack(side='left')
        ttk.Label(body,text='提醒在应用打开时检查；应用关闭后不会在后台提醒。\n手机需开启开发者模式并保持解锁。Xcode、信任和系统验证需要首次手动完成。',wraplength=710).pack(anchor='w',pady=(0,12))
        actions=ttk.Frame(footer);actions.pack(fill='x')
        self.install_button=ttk.Button(actions,text='安装 / 重新签名控制组件',command=self.install)
        self.install_button.pack(side='left')
        self.use_button=ttk.Button(actions,text='使用所选设备',command=self.use_device)
        self.use_button.pack(side='left',padx=8)
        verify_row=ttk.Frame(footer);verify_row.pack(fill='x',pady=(6,0))
        self.verify_button=ttk.Button(verify_row,text='重新验证已安装组件（不重新签名）',command=self.verify,state='disabled');self.verify_button.pack(side='left')
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.after(200,self.poll);self.window.after(15000,self.auto_scan)
        self.scan()

    def resize_form(self,event,body):
        self.canvas.itemconfigure(self.form_id,width=event.width)
        for widget in body.winfo_children():
            if isinstance(widget,ttk.Label):widget.configure(wraplength=max(260,event.width-48))
        self.status_label.configure(wraplength=max(260,self.window.winfo_width()-48))

    def scroll_form(self,event):
        if event.widget.winfo_class() in ('TCombobox','TSpinbox'):return
        if self.canvas.yview()!=(0.0,1.0) and event.delta:
            self.canvas.yview_scroll(-1 if event.delta>0 else 1,'units')

    def selected(self):
        index=self.device_box.current()
        return self.devices[index] if 0<=index<len(self.devices) else None

    def task(self,job):
        if self.busy:return
        self.busy=True;self.install_button.configure(state='disabled');self.use_button.configure(state='disabled')
        if hasattr(self,'verify_button'):self.verify_button.configure(state='disabled')
        self.wireless_button.configure(state='disabled');self.wireless_check.configure(state='disabled')
        self.device_box.configure(state='disabled');self.team_box.configure(state='disabled');self.progress.start(12)
        selected=self.selected()
        log_root=dm.device_dir(selected['udid']) if selected else DATA
        def work():
            try:job()
            except Exception as exc:
                self.events.put(('error',str(exc)))
                log_root.mkdir(parents=True,exist_ok=True)
                with (log_root/'device-setup.log').open('a') as log:
                    log.write(str(exc)+'\n'+str(exc.__cause__ or '')[:5000]+'\n')
            finally:self.events.put(('done',None))
        threading.Thread(target=work,daemon=True).start()

    def scan(self):
        if self.busy:return
        def job():
            directory=dm.check_xcode()
            if FROZEN:dm.prepare_vendor(lambda message:self.events.put(('status',message)))
            devices=dm.scan_devices();teams=dm.signing_teams()
            for device in devices:
                if device.get('paired'):
                    try:device['controller_apps']=dm.controller_apps(device['udid'])
                    except (RuntimeError,subprocess.SubprocessError,OSError):device['controller_apps']=None
            self.events.put(('scanned',(directory,devices,teams)))
        if not getattr(self,'last_result',''):self.status.set('正在检测 Xcode、USB 设备与签名团队…')
        self.task(job)

    def auto_scan(self):
        if not self.window.winfo_exists():return
        if not self.busy and not self.awaiting_wireless and not (self.panel.worker and self.panel.worker.is_alive()):self.scan()
        self.window.after(15000,self.auto_scan)

    def show_device(self):
        device=self.selected()
        if not device:
            self.detail.set('未检测到 USB iPhone。请连接、解锁、信任此电脑，然后重新检测。')
            self.signature.set('尚未配置设备。');return
        self.detail.set(f"{device['model']} · iOS {device['ios']} · 电脑信任配对：{'已完成' if device['paired'] else '待完成'} · 开发者模式：{'已开启' if device.get('developer') is True else '未开启' if device.get('developer') is False else '尚未确认'}\n设置 → 隐私与安全性 → 开发者模式；开启后需重启并确认。")
        self.wireless_status.set(ws.state(device['udid']).get('message','无线连接尚未准备；安装成功后可自动配置。'))
        apps=device.get('controller_apps')
        inventory=('手机上未发现控制组件；VPN 与设备管理没有对应开发者项是正常的，请先安装。' if apps==[] else '手机上检测到控制组件；是否可运行仍需连接验证。' if apps else '控制组件安装状态暂未读取到，请保持解锁后重新检测。')
        self.detail.set(self.detail.get()+'\n'+inventory)
        problem=dm.compatibility_error(device)
        if problem:self.detail.set(self.detail.get()+'\n'+problem)
        pending=dm.setup_status(device['udid'])
        if hasattr(self,'verify_button'):self.verify_button.configure(state='normal' if pending and pending.get('can_verify') and not self.busy else 'disabled')
        record=dm.signing_record(device['udid']);state,remaining=dm.expiry_state(record,int(self.panel.cfg.get('signature_warn_hours',48)))
        self.signature.set('签名：尚未安装，请点击下方安装按钮。' if not record else
            f"签名：{'已到期' if state=='expired' else '即将到期' if state=='soon' else '有效'} · 到期 {record['expires_at'][:10]} · 团队 {record['team_id']}")
        if pending and pending.get('status')!='ready':
            message=pending.get('message','请重新检测')
            if pending.get('code') in ('developer_mode','developer_mode_unconfirmed') and device.get('developer') is True:message='开发者模式已重新确认开启，请继续安装或验证组件。'
            self.signature.set('配置阶段：'+pending.get('stage','未完成')+' · '+message)
        if record:
            if hasattr(self,'verify_button') and not self.busy and state not in ('unknown','expired'):self.verify_button.configure(state='normal')
            team=record['team_id'];self.team.set(next((label for label,value in self.team_map.items() if value==team),team))

    def install(self):
        if self.busy:return
        if self.panel.worker and self.panel.worker.is_alive():
            self.status.set('请先停止浏览任务，再安装控制组件。');return
        device=self.selected()
        if not device:self.status.set('请先连接并选择 USB iPhone。');return
        team=self.team_map.get(self.team.get(),self.team.get().strip())
        if not __import__('re').fullmatch('[A-Z0-9]{10}',team):
            self.status.set('请选择签名团队，或输入正确的 10 位 Team ID。');return
        self.wireless_stop.set();self.awaiting_wireless=None
        auto=self.auto_wireless.get()
        def job():
            record=dm.provision(device,team,lambda message:self.events.put(('status',message)))
            self.finish_install(device,record,auto)
        self.last_result=''
        self.status.set('正在安装并验证控制组件；手机出现图标后仍需等待完成。')
        self.task(job)

    def verify(self):
        if self.busy:return
        if self.panel.worker and self.panel.worker.is_alive():self.status.set('请先停止浏览，再验证控制组件。');return
        device=self.selected()
        if not device:self.status.set('请先连接并选择 USB iPhone。');return
        self.wireless_stop.set();self.awaiting_wireless=None
        auto=self.auto_wireless.get()
        def job():
            record=dm.verify_provision(device,lambda message:self.events.put(('status',message)))
            self.finish_install(device,record,auto)
        self.last_result='';self.status.set('正在验证已安装组件，保留现有签名和安装。');self.task(job)

    def finish_install(self,device,record,auto):
        result=None;error=None
        if auto and str(device.get('ios','')).split('.')[0]=='16':
            error='iOS 16 本地无线控制暂不支持，请保留 USB 使用'
        elif auto:
            try:result=ws.prepare(device['udid'],lambda message:self.events.put(('status',message)))
            except Exception as exc:error=str(exc)
        self.events.put(('installed',(device,record)))
        if result:self.events.put(('wireless_prepared',device))
        elif error:self.events.put(('wireless_failed','组件已就绪，但无线准备未完成：'+error+'。USB 仍可使用；可点击“准备无线连接”重试。'))

    def prepare_wireless(self):
        if self.busy:return
        device=self.selected()
        if not device:self.status.set('请先连接并选择 USB iPhone。');return
        if str(device.get('ios','')).split('.')[0]=='16':self.status.set('iOS 16 本地无线控制暂不支持，请保留 USB 使用。');return
        if self.panel.worker and self.panel.worker.is_alive():self.status.set('请先停止该手机的浏览任务，再准备无线连接。');return
        self.wireless_stop.set();self.wireless_stop=threading.Event();self.awaiting_wireless=None
        def job():
            ws.prepare(device['udid'],lambda message:self.events.put(('status',message)))
            self.events.put(('wireless_prepared',device))
        self.task(job)

    def wait_for_unplug(self,device):
        self.wireless_stop.set();self.wireless_stop=threading.Event();stop=self.wireless_stop
        self.awaiting_wireless=device
        message=ws.state(device['udid']).get('message','请拔掉 USB，保持同一 Wi-Fi 和解锁。')
        self.wireless_status.set(message);self.last_result=message;self.status.set(message)
        def watch():
            while not stop.wait(1):
                try:
                    if not ws.wired(device['udid']):
                        if not stop.is_set():self.events.put(('wireless_unplugged',(device,stop)))
                        return
                except OSError:pass
        threading.Thread(target=watch,daemon=True).start()

    def use_device(self):
        device=self.selected()
        if not device:
            self.status.set('未选择 USB iPhone，请连接手机并重新检测。');return
        if self.panel.worker and self.panel.worker.is_alive():self.status.set('请先停止当前浏览任务。');return
        blocker=dm.setup_blocker(device['udid'])
        if blocker:
            self.status.set(blocker);self.last_result=blocker;return
        record=dm.signing_record(device['udid'])
        if dm.expiry_state(record)[0] in ('unknown','expired'):
            message='当前设备没有有效安装记录，无法开始浏览。请先点击“安装 / 重新签名控制组件”，等待显示签名有效；失败详情见 appium.log。'
            self.status.set(message);self.last_result=message
            messagebox.showwarning('设备配置尚未完成',message,parent=self.window);return
        if not self.panel.save_settings(quiet=True):return
        callback=self.panel.__dict__.get('on_device_selected')
        if callback:
            callback(device);self.last_result='设备已添加，可在总览独立开始。';self.status.set(self.last_result);return
        self.panel.cfg.update(device_udid=device['udid'],device_name=device['name'],runtime_dir=str(dm.device_dir(device['udid'])))
        dm.write_json(self.panel.__dict__.get('config_path',DATA/'config.json'),self.panel.cfg)
        self.panel.device_label.set(device['name']+' · USB 实机控制 · 手动运行')
        self.panel.status.set('设备已配置，可点击开始浏览。');self.panel.update_signature()
        self.last_result='已选择 '+device['name']+'。现在可以关闭本窗口并开始浏览。'
        self.status.set(self.last_result)

    def save_reminder(self):
        try:
            hours=int(self.hours.get())
            if not 1<=hours<=168:raise ValueError()
        except ValueError:self.status.set('提醒时间须为 1～168 小时。');return
        self.panel.cfg['signature_warn_hours']=hours
        dm.write_json(self.panel.__dict__.get('config_path',DATA/'config.json'),self.panel.cfg);self.panel.update_signature()
        self.status.set('提醒设置已保存。')

    def open_xcode(self):
        try:subprocess.Popen(['/usr/bin/open',str(dm.xcode_directory().parent.parent)])
        except RuntimeError:subprocess.Popen(['/usr/bin/open','macappstore://apps.apple.com/app/id497799835'])

    def open_logs(self):
        device=self.selected();folder=dm.device_dir(device['udid']) if device else DATA
        folder.mkdir(parents=True,exist_ok=True);subprocess.Popen(['/usr/bin/open',str(folder)])

    def poll(self):
        if not self.window.winfo_exists():return
        try:
            while True:
                event,value=self.events.get_nowait()
                if event=='scanned':
                    directory,devices,teams=value
                    if self.panel.__dict__.get('pinned_device'):devices=[d for d in devices if d['udid']==self.panel.cfg['device_udid']]
                    previous=self.selected();old_udid=previous['udid'] if previous else self.panel.cfg.get('device_udid')
                    old_team=self.team.get();self.devices=devices
                    self.environment.set('Xcode 已找到：'+str(directory.parent.parent)+' · 内置运行依赖已就绪')
                    self.device_box.configure(values=[d['name']+' · '+d['model']+' · '+d['udid'][-8:] for d in devices])
                    if devices:self.device_box.current(next((i for i,d in enumerate(devices) if d['udid']==old_udid),0))
                    else:self.device.set('')
                    self.team_map={name+' · '+team:team for team,name in teams.items()}
                    self.team_box.configure(values=list(self.team_map))
                    self.team.set(old_team or next(iter(self.team_map),''))
                    self.show_device()
                    if not getattr(self,'last_result',''):self.status.set('检测完成。选择设备与签名团队后安装。')
                elif event=='installed':
                    device,record=value
                    for current in self.devices:
                        if current['udid']==device['udid']:current['controller_apps']=[{'bundle':record.get('bundle_id','')}]
                    self.show_device();self.use_device()
                    if dm.expiry_state(record,self.panel.cfg.get('signature_warn_hours',48))[0]=='soon':
                        self.status.set('重新签名安装已完成，但有效期仍临近到期。请在 Xcode 更新证书或描述文件后重试。')
                elif event=='wireless_prepared':self.wait_for_unplug(value)
                elif event=='wireless_failed':
                    self.last_result=value;self.status.set(value)
                elif event=='wireless_unplugged':
                    device,stop=value
                    if stop.is_set() or self.awaiting_wireless is not device:continue
                    self.awaiting_wireless=None;self.wireless_running=True
                    def job(device=device,stop=stop):
                        result=ws.verify(device['udid'],stop,lambda message:self.events.put(('status',message)))
                        self.events.put(('wireless_verified',result))
                    self.task(job)
                elif event=='wireless_verified':
                    self.last_result=value['message'];self.status.set(value['message']);self.wireless_status.set(value['message'])
                    if hasattr(self.panel,'store'):self.panel.refresh()
                elif event=='done':
                    self.busy=False;self.wireless_running=False;self.progress.stop()
                    self.wireless_button.configure(state='normal');self.wireless_check.configure(state='normal')
                    self.install_button.configure(state='normal');self.use_button.configure(state='normal')
                    self.device_box.configure(state='readonly');self.team_box.configure(state='normal')
                    self.show_device()
                elif event=='error':
                    self.last_result=value;self.status.set(value)
                else:self.status.set(value)
        except queue.Empty:pass
        self.window.after(200,self.poll)

    def close(self):
        if self.busy:
            if self.wireless_running:self.wireless_stop.set();self.status.set('正在取消无线验证，请等待当前检查结束后关闭。');return
            self.status.set('配置仍在进行，请等待结束后关闭。');return
        self.wireless_stop.set();self.awaiting_wireless=None
        self.panel.device_window=None;self.window.destroy()
