"""Configure one explicit transport without changing browsing preferences."""
import json,queue,threading
import tkinter as tk
from tkinter import ttk,filedialog
from urllib.parse import urlsplit
import connections as cn
import network_profiles as np
import device_manager as dm
import ui_theme as ui
from app_paths import APP_VERSION

class ConnectionWindow:
    def __init__(self,fleet,udid=None):
        self.fleet=fleet;self.udid=udid;self.busy=False;self.events=queue.Queue();self.devices=[];self.stop_event=threading.Event()
        if not hasattr(fleet,'connection_windows'):fleet.connection_windows=[]
        fleet.connection_windows.append(self)
        self.window=tk.Toplevel(fleet.root);self.window.title(f'连接方式 · {APP_VERSION}');self.window.geometry('780x700');self.window.minsize(620,540)
        footer=ttk.Frame(self.window,padding=18);footer.pack(side='bottom',fill='x')
        self.status=tk.StringVar(value='发现局域网中已运行的 WDA，或导入原 Mac 导出的连接文件。')
        self.status_label=ttk.Label(footer,textvariable=self.status,wraplength=720);self.status_label.pack(fill='x',pady=(0,10))
        self.button=ttk.Button(footer,text='验证并保存连接方式',command=self.save,style='Primary.TButton');self.button.pack(side='left')
        self.close_button=ttk.Button(footer,text='关闭',command=self.close);self.close_button.pack(side='right')
        body=ui.scroll_form(self.window,22)
        ttk.Label(body,text='连接手机',style='Title.TLabel').pack(anchor='w',pady=(0,12))
        try:initial=cn.load(udid) if udid else {'mode':'wifi'}
        except ValueError:initial={'mode':'usb'};self.status.set('连接配置异常，请先选择 USB 保存，再重新验证无线连接。')
        tools=ttk.Frame(body);tools.pack(fill='x',pady=(0,12))
        self.import_button=ttk.Button(tools,text='导入连接配置…',command=self.import_config);self.import_button.pack(side='left')
        self.export_button=ttk.Button(tools,text='导出此设备…' if udid else '导出全部无线连接…',command=self.export_config);self.export_button.pack(side='left',padx=8)
        self.scan_pin=None
        self.identity_kind=initial.get('identifier_kind','udid')
        self.mode=tk.StringVar(value=cn.MODES[initial['mode']])
        self.mode_box=ttk.Combobox(body,textvariable=self.mode,values=list(cn.MODES.values()),state='readonly');self.mode_box.pack(fill='x');self.mode_box.bind('<<ComboboxSelected>>',lambda _:self.explain())
        self.help=tk.StringVar();ttk.Label(body,textvariable=self.help,wraplength=700).pack(fill='x',pady=(8,16))
        row=ttk.Frame(body);row.pack(fill='x');ttk.Label(row,text='发现的设备',style='Section.TLabel').pack(side='left');self.scan_button=ttk.Button(row,text='发现局域网设备',command=self.scan);self.scan_button.pack(side='right')
        self.device=tk.StringVar();self.box=ttk.Combobox(body,textvariable=self.device,state='readonly');self.box.pack(fill='x',pady=(8,12));self.box.bind('<<ComboboxSelected>>',lambda _:self.select())
        self.selection_summary=tk.StringVar(value='选择发现的设备即可连接；看不到设备时可导入原 Mac 的配置。')
        ttk.Label(body,textvariable=self.selection_summary,wraplength=700,style='Muted.TLabel').pack(fill='x',pady=(0,8))
        self.show_details=tk.BooleanVar(value=False)
        ttk.Checkbutton(body,text='显示连接详情 / 手动填写',variable=self.show_details,command=self.toggle_details).pack(anchor='w',pady=(4,8))
        self.detail_frame=ttk.Frame(body)
        self.values={};self.entries=[]
        self.remote_frame=ttk.Frame(self.detail_frame)
        cfg=next((c for c in fleet.store.profiles() if c['device_udid']==udid),{}) if udid else {}
        setting=initial
        for key,label,value in [('udid','设备 UDID',udid or ''),('name','显示名称',cfg.get('device_name','iPhone')),('ios','iOS 版本',cfg.get('device_ios','')),('wda_url','远程 WDA 地址',setting.get('wda_url','')),('preview_url','远程 MJPEG 地址（可留空）',setting.get('preview_url',''))]:
            parent=self.remote_frame if key in ('wda_url','preview_url') else self.detail_frame
            field_label=ttk.Label(parent,text=label);field_label.pack(anchor='w',pady=(7,3))
            if key=='udid':self.identity_label=field_label
            var=tk.StringVar(value=value);self.values[key]=var
            entry=ttk.Entry(parent,textvariable=var);entry.pack(fill='x');self.entries.append((key,entry))
            if key=='udid' and udid:entry.configure(state='readonly')
        self.remote_frame.pack(fill='x')
        self.notice=ttk.Label(body,text='自动发现检查当前局域网的常用 WDA 端口（18100、8100）及已保存地址；自定义端口可通过配置导入。手机需保持解锁、组件运行。远程模式仅连接已运行的 WDA，不代替远端配对、启动、续签或重启恢复。只填可信局域网 / Tailscale 地址。设备指纹用于防止误连，不替代私网访问控制。',style='Muted.TLabel',wraplength=700);self.notice.pack(fill='x',pady=(14,0))
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.window.bind('<Configure>',lambda e:self.status_label.configure(wraplength=max(260,self.window.winfo_width()-48)) if e.widget==self.window else None)
        self.window.bind('<Command-i>',lambda _:self.import_config())
        self.window.bind('<Command-e>',lambda _:self.export_config())
        self.window.bind('<Command-r>',lambda _:self.scan())
        self.explain();self.window.after(150,self.poll);self.scan()
    def explain(self):
        if self.mode.get()=='远程已有 WDA':self.remote_frame.pack(fill='x')
        else:self.remote_frame.pack_forget()
        mode=self.mode.get()
        self.identity_label.configure(text='连接标识（由设备指纹生成，无需填写）' if self.identity_kind=='wda' else '设备 UDID（发现或导入后自动填写）')
        self.help.set({'USB':'保持原有 USB 控制。适用于首次安装、重新签名和 iOS 16 兼容组件。','本地 Wi-Fi':'无线功能仍在实机验证中。手机与 Mac 位于可互通的局域网，使用已配对的苹果开发连接自动启动本机已签名组件。首次仍需 USB 安装；目前自动启动支持 iOS 17 及以上。地址自动解析，不用填写远程地址。','远程已有 WDA':'发现并选择设备后，名称、系统和连接地址会自动填写。新 Mac 导入配置后即可验证连接，无需原签名证书。手机重启后需由已配对的 Mac 恢复 WDA 运行。'}[mode])
    def toggle_details(self):
        if self.show_details.get():self.detail_frame.pack(fill='x',before=self.notice)
        else:self.detail_frame.pack_forget()
    def running(self,udid):
        panel=self.fleet.panels.get(udid)
        return panel and panel.worker and panel.worker.is_alive()
    def task(self,job):
        if self.busy:return
        for _,entry in self.entries:entry.configure(state='disabled')
        self.busy=True;self.import_button.configure(state='disabled');self.export_button.configure(state='disabled');self.button.configure(state='disabled');self.scan_button.configure(state='disabled');self.mode_box.configure(state='disabled');self.box.configure(state='disabled')
        def work():
            try:self.events.put(('result',job()))
            except Exception as e:self.events.put(('error',str(e) or type(e).__name__))
            finally:self.events.put(('done',None))
        threading.Thread(target=work,daemon=True).start()
    def scan(self):
        if self.busy:return
        self.status.set('正在发现局域网中已运行的 WDA…')
        profiles=self.fleet.store.profiles()
        def job():
            result=np.discover_services(profiles,lambda s:self.events.put(('status',s)),self.stop_event)
            try:paired=cn.discover()
            except Exception:paired=[]
            services=[]
            for value in result['devices']:
                item=np.normalize_device(value)
                for cfg in profiles:
                    try:old=cn.load(cfg['device_udid'])
                    except ValueError:continue
                    if old.get('fingerprint')==item['fingerprint']:
                        item['udid']=cfg['device_udid'];item['identifier_kind']=old.get('identifier_kind','udid');item['name']=cfg.get('device_name',item['name']);break
                services.append({**value,**item,'service':True,'network':True})
            ids={d['udid'] for d in services}
            return ('devices',{'devices':services+[d for d in paired if d['udid'] not in ids],'networks':result['networks']})
        self.task(job)
    def select(self):
        i=self.box.current()
        if i<0 or i>=len(self.devices):return
        d=self.devices[i]
        if self.udid and d['udid']!=self.udid:return
        for key in ('udid','name','ios','wda_url','preview_url'):
            if key in d:self.values[key].set(d[key])
        self.scan_pin=d.get('fingerprint');self.identity_kind=d.get('identifier_kind','udid')
        if d.get('service'):
            # Keep a local managed connection when selecting its own discovered endpoint.
            self.mode.set(cn.MODES['wifi' if self.udid and cn.load(self.udid)['mode']=='wifi' else 'remote'])
        self.selection_summary.set(d['name']+' · iOS '+d['ios']+('\n'+d['wda_url'] if d.get('wda_url') else '\n已配对设备，验证时会尝试启动本机签名组件。'))
        self.explain()
    def export_config(self):
        if self.busy:return
        profiles=self.fleet.store.profiles()
        if self.udid:profiles=[p for p in profiles if p['device_udid']==self.udid]
        try:package=np.export_package(profiles)
        except (ValueError,OSError) as exc:self.status.set(str(exc));return
        path=filedialog.asksaveasfilename(parent=self.window,title='导出无线连接配置',initialfile='iPhone-连接配置.json',defaultextension='.json',filetypes=[('连接配置','*.json')])
        if not path:return
        try:
            from pathlib import Path
            Path(path).write_text(json.dumps(package,ensure_ascii=False,indent=2)+'\n')
            self.status.set(f'已导出 {len(package["devices"])} 台设备的连接配置。包含私网地址和设备指纹，不含证书、私钥或账号凭据；请仅交给需要控制设备的人。')
        except OSError as exc:self.status.set('导出失败：'+str(exc))
    def import_config(self):
        if self.busy:return
        path=filedialog.askopenfilename(parent=self.window,title='导入无线连接配置',filetypes=[('连接配置','*.json')])
        if not path:return
        try:
            items=np.read_package(path)
            if self.udid:
                pin=cn.load(self.udid).get('fingerprint')
                items=[d for d in items if d['udid']==self.udid or (pin and d['fingerprint']==pin)]
                if not items:raise ValueError('文件中没有当前设备。请从工作区“添加无线设备”导入其他手机。')
        except (OSError,ValueError) as exc:self.status.set('未导入：'+str(exc));return
        self.status.set(f'正在验证 {len(items)} 台设备；原配置会保留到验证成功…')
        def job():return ('imported',np.import_devices(self.fleet.store,items,self.running,lambda s:self.events.put(('status',s)),self.stop_event))
        self.task(job)
    def save(self):
        if self.busy:return
        values={k:v.get().strip() for k,v in self.values.items()};udid=values['udid'];mode=next(k for k,v in cn.MODES.items() if v==self.mode.get())
        try:
            dm.device_dir(udid)
            if self.udid and self.udid!=udid:raise ValueError('不能更改当前设备标识。')
            if self.running(udid):raise ValueError('请先停止这台手机的任务，再修改连接方式。')
            if self.identity_kind=='wda' and mode!='remote':raise ValueError('这台手机通过 WDA 指纹识别，请使用“远程已有 WDA”；本机签名和 USB 管理需先在设备向导配对。')
            if not values['name']:raise ValueError('请填写设备显示名称。')
            if mode=='remote':
                values['wda_url']=cn.validate_url(values['wda_url'])
                if values['preview_url']:values['preview_url']=cn.validate_url(values['preview_url'])
        except ValueError as e:self.status.set(str(e));return
        self.status.set('正在验证，完成前保留原连接配置…')
        def job():
            setting={'mode':mode}
            if mode=='usb':cn.stop_device(udid)
            if mode!='usb':
                url=values['wda_url'] if mode=='remote' else cn.ensure_wifi(udid,lambda s:self.events.put(('status',s)),stop=self.stop_event,setting_override={'mode':'wifi',**({'fingerprint':cn.load(udid)['fingerprint']} if cn.load(udid).get('fingerprint') else {})})
                info=cn.checked(url,self.scan_pin) if self.scan_pin else cn.inspect(url)
                old=cn.load(udid)
                if old.get('fingerprint') and old['fingerprint']!=info['fingerprint']:raise RuntimeError('设备指纹改变，未保存。请检查地址；如更换了签名组件，请先恢复 USB 配置后重新绑定。')
                setting.update(identifier_kind=self.identity_kind,fingerprint=info['fingerprint'],wda_url=url if mode=='remote' else '',preview_url=values['preview_url'] if mode=='remote' else '',verified_ios=info['ios'])
                values['ios']=info['ios'];values['reported_name']=info['name']
            cn.save(udid,setting)
            if mode=='wifi':dm.write_json(dm.device_dir(udid)/'setup-state.json',{'status':'ready','message':'无线 WDA 已启动并验证。'})
            return ('saved',{'udid':udid,'name':values['name'],'ios':values['ios'],'mode':mode})
        def locked_job():
            from app_paths import lock_device
            with lock_device(udid):return job()
        self.task(locked_job)
    def poll(self):
        if not self.window.winfo_exists():return
        try:
            while True:
                event,value=self.events.get_nowait()
                if event=='result':
                    kind,data=value
                    if kind=='devices':
                        self.devices=[d for d in data['devices'] if not self.udid or d['udid']==self.udid]
                        self.box.configure(values=[d['name']+' · '+d['ios']+' · '+(('WDA · '+str(urlsplit(d['wda_url']).hostname)) if d.get('service') else '已配对网络' if d.get('network') else 'USB') for d in self.devices])
                        if self.devices:self.box.current(0);self.select()
                        self.status.set('发现完成。选中设备后点击“验证并保存连接方式”，无需手动填写。' if self.devices else '未发现可用设备。请确认同一局域网、WDA 已运行；新 Mac 可先导入连接配置。首次安装组件仍需配对。')
                    elif kind=='imported':
                        if data['saved']:
                            for profile in data['saved']:self.fleet.errors.pop(profile['udid'],None)
                            self.fleet.refresh()
                        self.status.set(f'已验证并导入 {len(data["saved"])} 台设备。'+('\n未导入（原配置保留）：\n'+'\n'.join(data['failed'][:3])+('\n另有 '+str(len(data['failed'])-3)+' 台失败，请逐台检查连接。' if len(data['failed'])>3 else '') if data['failed'] else ' 回到工作区即可选择设备连接。'))
                    else:
                        self.fleet.store.add(data);self.udid=data['udid'];self.fleet.errors.pop(self.udid,None)
                        self.fleet.refresh();self.status.set('已保存 USB 方式，请在签名向导完成安装验证。' if data['mode']=='usb' else '连接方式已保存，WDA 服务检查通过。回到工作区建议先试运行 45 秒。')
                elif event=='error':self.status.set(value)
                elif event=='status':self.status.set(value)
                elif event=='done':
                    for key,entry in self.entries:entry.configure(state='readonly' if key=='udid' and self.udid else 'normal')
                    self.busy=False;self.import_button.configure(state='normal');self.export_button.configure(state='normal');self.button.configure(state='normal');self.scan_button.configure(state='normal');self.mode_box.configure(state='readonly');self.box.configure(state='readonly')
        except queue.Empty:pass
        self.window.after(150,self.poll)
    def close(self):
        if self.busy:self.status.set('正在检查连接，请等待本次检查结束后关闭。');return
        if self in self.fleet.connection_windows:self.fleet.connection_windows.remove(self)
        self.window.destroy()
