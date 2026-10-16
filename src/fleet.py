"""Native multi-device overview; each device retains its own complete console."""
import copy,json,queue,threading,time,subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from app_paths import DATA,load_config
import device_manager as dm
import ui_theme as ui

class DeviceStore:
    def __init__(self,root=None,template=None):
        self.root=Path(root or DATA);self.template=copy.deepcopy(template if template is not None else load_config());self.root.mkdir(parents=True,exist_ok=True)
        old=self.template.get('device_udid')
        if old and not self.path(old).exists():self.save(self.make({'udid':old,'name':self.template.get('device_name','iPhone'),'ios':self.template.get('device_ios','')}))
    def path(self,udid):
        import re
        if not re.fullmatch('[A-Fa-f0-9-]{10,50}',udid):raise ValueError('设备标识无效')
        return self.root/'devices'/udid/'config.json'
    def make(self,device):
        cfg=copy.deepcopy(self.template);udid=device['udid'];self.path(udid)
        cfg.update(device_udid=udid,device_name=device.get('name','iPhone'),device_ios=device.get('ios',''),runtime_dir=str(self.path(udid).parent),managed_device=True)
        cfg.pop('server_url',None);return cfg
    def save(self,cfg):
        value=copy.deepcopy(cfg);path=self.path(value['device_udid']);value['runtime_dir']=str(path.parent);value['managed_device']=True;dm.write_json(path,value)
    def add(self,device):
        path=self.path(device['udid'])
        cfg=json.loads(path.read_text()) if path.exists() else self.make(device)
        if device.get('name') and device.get('name')!='iPhone':cfg['device_name']=device['name']
        if device.get('ios') and device.get('ios')!='未知':cfg['device_ios']=device['ios']
        self.save(cfg);return cfg
    def profiles(self):
        rows=[]
        for path in sorted((self.root/'devices').glob('*/config.json')):
            try:
                cfg=json.loads(path.read_text());self.path(cfg['device_udid'])
                if path.parent.name!=cfg['device_udid']:continue
                cfg['runtime_dir']=str(path.parent);cfg['managed_device']=True;rows.append(cfg)
            except (OSError,ValueError,KeyError):
                try:cfg=self.make({'udid':path.parent.name,'name':'配置文件异常'});cfg['_load_error']='配置文件无法读取，请检查该设备 config.json。';rows.append(cfg)
                except ValueError:continue
        return rows

class Fleet:
    def __init__(self,root,store=None):
        self.root=root;self.store=store or DeviceStore();self.panels={};self.online={};self.errors={};self.events=queue.Queue();self.scanning=False;self.closing=False;self.device_window=None
        self.cfg=copy.deepcopy(self.store.template);self.cfg['device_udid']='';self.worker=None
        self.on_device_selected=self.selected_device_ready;self.signature_notices=set()
        self.pending_starts={};self.pending_all=None;self.last_refresh=None;self._view_cache={}
        ui.apply(root);root.title('Instagram 控制台 · 设备工作区')
        root.geometry(f'{min(1260,root.winfo_screenwidth()-60)}x{min(780,root.winfo_screenheight()-100)}');root.minsize(1000,640)
        body=ttk.Frame(root,padding=24,style='Workspace.TFrame');body.pack(fill='both',expand=True)
        header=ttk.Frame(body,style='Workspace.TFrame');header.pack(fill='x',pady=(0,18))
        title=ttk.Frame(header,style='Workspace.TFrame');title.pack(side='left')
        ttk.Label(title,text='设备工作区',font=('PingFang SC',22,'bold'),background=ui.BG,foreground=ui.INK).pack(anchor='w')
        self.summary=tk.StringVar(value='正在检测 USB 连接…')
        ttk.Label(title,textvariable=self.summary,style='Workspace.TLabel').pack(anchor='w',pady=(5,0))
        self.add_button=ttk.Button(header,text='添加设备',command=self.open_devices);self.add_button.pack(side='right',anchor='n')
        workspace=ttk.Frame(body,style='Workspace.TFrame');workspace.pack(fill='both',expand=True)
        from preview import PreviewPane
        self.preview=PreviewPane(workspace);self.preview.frame.pack(side='right',fill='y',padx=(16,0))
        sheet=ttk.Frame(workspace,padding=18);sheet.pack(side='left',fill='both',expand=True)
        toolbar=ttk.Frame(sheet);toolbar.pack(fill='x',pady=(0,14))
        ttk.Label(toolbar,text='USB 设备',style='Section.TLabel').pack(side='left')
        self.refresh_button=ttk.Button(toolbar,text='刷新连接',command=self.refresh);self.refresh_button.pack(side='left',padx=12)
        self.stop_all_button=ttk.Button(toolbar,text='全部停止',command=self.stop_all);self.stop_all_button.pack(side='right')
        self.start_all_button=ttk.Button(toolbar,text='全部开始',command=self.start_all,style='Primary.TButton');self.start_all_button.pack(side='right',padx=(0,8))
        self.scan_progress=ttk.Progressbar(sheet,mode='indeterminate');self.scan_progress.pack(fill='x',pady=(0,8))
        self.table_area=ttk.Frame(sheet);self.table_area.pack(fill='both',expand=True)
        self.table=ttk.Treeview(self.table_area,columns=('device','system','connection','state','progress'),show='headings',selectmode='browse',height=6,style='Fleet.Treeview')
        for key,title,width in [('device','设备',210),('system','系统',100),('connection','连接',100),('state','状态',140),('progress','本轮进度',190)]:
            self.table.heading(key,text=title,anchor='w');self.table.column(key,width=width,minwidth=80,stretch=key in ('device','progress'))
        scroll=ttk.Scrollbar(self.table_area,orient='vertical',command=self.table.yview);self.table.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');self.table.pack(fill='both',expand=True)
        for tag,color in [('error',ui.RED),('running',ui.GREEN),('pending',ui.AMBER),('idle',ui.INK)]:self.table.tag_configure(tag,foreground=color)
        self.table.bind('<<TreeviewSelect>>',lambda _:self.update_detail());self.table.bind('<Double-1>',lambda _:self.open_selected());self.table.bind('<Return>',lambda _:self.open_selected())
        self.empty=ttk.Frame(self.table_area,padding=24)
        ttk.Label(self.empty,text='连接第一台 iPhone',font=('PingFang SC',18,'bold')).pack(pady=(24,10))
        ttk.Label(self.empty,text='通过 USB 连接手机，解锁并信任此电脑。\n检测到设备后，在向导中完成控制组件安装。',justify='center',style='Muted.TLabel').pack(pady=(0,16))
        ttk.Button(self.empty,text='打开设备向导',command=self.open_devices).pack()
        detail=ttk.Frame(sheet);self.detail_frame=detail;detail.pack(side='bottom',fill='x',before=self.table_area)
        ttk.Separator(detail).pack(fill='x',pady=(12,12))
        self.detail_title=tk.StringVar(value='选择设备');self.detail_meta=tk.StringVar(value='在上方列表选择一台手机，查看状态或单独操作。')
        self.detail_status=tk.StringVar(value='')
        ttk.Label(detail,textvariable=self.detail_title,style='Section.TLabel').pack(anchor='w')
        ttk.Label(detail,textvariable=self.detail_meta,style='Muted.TLabel').pack(anchor='w',pady=(4,7))
        self.detail_label=ttk.Label(detail,textvariable=self.detail_status,wraplength=920);self.detail_label.pack(fill='x',pady=(0,12))
        actions=ttk.Frame(detail);actions.pack(fill='x')
        self.start_selected_button=ttk.Button(actions,text='开始此设备',command=self.start_selected,style='Primary.TButton');self.start_selected_button.pack(side='left')
        self.stop_selected_button=ttk.Button(actions,text='停止此设备',command=self.stop_selected);self.stop_selected_button.pack(side='left',padx=8)
        self.config_button=ttk.Button(actions,text='配置与日志',command=self.open_selected);self.config_button.pack(side='left')
        self.setup_button=ttk.Button(actions,text='连接与签名',command=self.setup_selected);self.setup_button.pack(side='right')
        footer=ttk.Frame(body,style='Workspace.TFrame');footer.pack(side='bottom',fill='x',pady=(12,0),before=workspace)
        self.status=tk.StringVar(value='连接检测通常需要几秒，期间可以安排开始。')
        self.status_label=ttk.Label(footer,textvariable=self.status,wraplength=850,style='Workspace.TLabel');self.status_label.pack(fill='x')
        ttk.Label(footer,text='⌘ Return 全部开始    Esc 全部停止    双击设备打开配置',style='Workspace.TLabel',font=('PingFang SC',10)).pack(anchor='w',pady=(6,0))
        self.device_label=tk.StringVar();root.protocol('WM_DELETE_WINDOW',self.close);root.createcommand('tk::mac::Quit',self.close)
        root.bind('<Escape>',lambda _:self.stop_all());root.bind('<Command-Return>',lambda _:self.start_all())
        root.bind('<Configure>',lambda event:self.resize(event),add='+')
        root.bind('<Destroy>',lambda e:self.preview.close() if e.widget==root else None,add='+')
        self.update_detail();root.after(100,self.refresh);root.after(200,self.poll)
    def resize(self,event):
        if event.widget!=self.root:return
        available=max(480,event.width-410)
        self.detail_label.configure(wraplength=available);self.status_label.configure(wraplength=max(650,event.width-60))
        self.table.configure(displaycolumns=('device','state','progress') if event.width<1150 else ('device','system','connection','state','progress'))
    def save_settings(self,quiet=False):return True
    def update_signature(self):pass
    def selected_device_ready(self,device):
        self.store.add(device);self.errors.pop(device['udid'],None);self.redraw();self.status.set('设备已添加：'+device['name'])
    def open_devices(self):
        if self.device_window:self.device_window.window.lift();return
        from device_ui import DeviceWindow
        self.device_window=DeviceWindow(self)
    def refresh(self):
        if self.scanning or self.closing:return
        self.scanning=True;self.refresh_button.configure(state='disabled',text='正在检测…');self.scan_progress.pack(fill='x',pady=(0,8),before=self.table_area);self.scan_progress.start(15);self.status.set('正在检测 USB 连接，可安排检测完成后开始。');self.redraw()
        def scan():
            try:self.events.put(('devices',dm.scan_devices()))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=scan,daemon=True).start()
    def device_view(self,cfg):
        udid=cfg['device_udid'];p=self.panels.get(udid);record=dm.signing_record(udid)
        alive=bool(p and p.worker and p.worker.is_alive());stopping=bool(alive and p.stop_event.is_set())
        online=self.online.get(udid);ready=bool(online and online.get('paired') and online.get('developer'))
        detail=self.errors.get(udid) or cfg.get('_load_error') or (p.status.get() if p else '')
        signature,_=dm.expiry_state(record)
        if udid in self.__dict__.get('pending_starts',{}) or self.__dict__.get('pending_all') is not None:state,tag='等待连接检测','pending'
        elif self.scanning and not online:state,tag='检测连接中','pending'
        elif stopping:state,tag='正在停止','pending'
        elif alive:
            search=getattr(getattr(p,'runner',None),'feature_state',{}).get('search',{})
            if not search.get('enabled',True) and search.get('reason'):
                state,tag='浏览中 · 搜索暂停','pending';detail+='\n搜索已暂停：'+search['reason']
            else:state,tag='运行中','running'
        elif self.errors.get(udid) or cfg.get('_load_error') or (detail.startswith('已停止：')):state,tag='需要处理','error'
        elif not online:state,tag='设备未连接','pending'
        elif not ready:state,tag='检查连接','pending'
        elif signature in ('unknown','expired'):state,tag='待配置' if signature=='unknown' else '签名已到期','pending'
        elif signature=='soon':state,tag='签名即将到期','pending'
        else:state,tag='已就绪','idle'
        if not detail:
            detail=('检测完成后将自动开始。' if self.scanning else '请连接这台手机并刷新连接。') if not online else ('请打开“连接与签名”完成解锁、信任或签名配置。' if tag=='pending' else '可以开始浏览，或打开配置调整此设备的设置。')
        if self.scanning and not online:detail='已安排开始，连接检测完成后自动执行。' if self.pending_all is not None or udid in self.pending_starts else '正在检测此设备的 USB 连接，请稍候。'
        if not alive and not online and not self.scanning:detail='请连接这台手机并刷新连接。'+(' '+detail if self.errors.get(udid) else '')
        return dict(state=state,tag=tag,detail=detail,alive=alive,stopping=stopping,ready=ready,signature=signature,panel=p)
    def redraw(self):
        profiles=self.store.profiles();self._view_cache={c['device_udid']:(c,self.device_view(c)) for c in profiles};ids=set(self._view_cache)
        for udid in self.table.get_children():
            if udid not in ids:self.table.delete(udid)
        running=attention=0
        for udid,(cfg,v) in self._view_cache.items():
            p=v['panel'];running+=v['alive'];attention+=v['tag'] in ('error','pending')
            stats=f'翻页 {p.runner.swipes}   ·   搜索 {p.runner.searches}' if p and hasattr(p,'runner') else '尚未运行'
            values=(cfg.get('device_name','iPhone'),'iOS '+cfg.get('device_ios','未知'),'USB 就绪' if v['ready'] else '检测中' if self.scanning and udid not in self.online else '待检查' if udid in self.online else '离线',v['state'],stats)
            if self.table.exists(udid):
                if tuple(self.table.item(udid,'values'))!=values:self.table.item(udid,values=values,tags=(v['tag'],))
            else:self.table.insert('', 'end',iid=udid,values=values,tags=(v['tag'],))
        if profiles:
            self.detail_frame.pack(side='bottom',fill='x',before=self.table_area)
            self.empty.pack_forget();self.table.pack(fill='both',expand=True)
            if not self.table.selection():self.table.selection_set(profiles[0]['device_udid'])
        else:
            self.detail_frame.pack_forget();self.table.pack_forget();self.empty.pack(fill='both',expand=True)
        self.summary.set(f'正在检测连接   ·   已保存 {len(profiles)} 台设备' if self.scanning else f'{len(profiles)} 台设备   ·   {running} 台运行中   ·   {attention} 台需处理' if profiles else '通过 USB 连接手机，分别配置与运行')
        self.start_all_button.configure(state='disabled' if self.closing or not profiles else 'normal',text='检测后全部开始' if self.scanning else '全部开始')
        waiting=bool(self.pending_starts) or self.pending_all is not None
        self.stop_all_button.configure(state='normal' if running or waiting else 'disabled')
        self.update_detail()
    def update_detail(self):
        selected=self.table.selection();udid=selected[0] if selected else None
        item=self._view_cache.get(udid)
        if self.__dict__.get('preview'):self.preview.select(udid,item[0].get('device_name','iPhone') if item else '手机画面')
        if not item:
            self.detail_title.set('尚未选择设备');self.detail_meta.set('');self.detail_status.set('请选择要操作的设备。')
            for button in (self.start_selected_button,self.stop_selected_button,self.config_button,self.setup_button):button.configure(state='disabled')
            return
        cfg,v=item;self.detail_title.set(cfg.get('device_name','iPhone'))
        self.detail_meta.set('iOS '+cfg.get('device_ios','未知')+'   ·   '+('USB 已就绪' if v['ready'] else 'USB 检测中' if self.scanning else 'USB 未就绪')+'   ·   '+{'valid':'签名有效','soon':'签名即将到期','expired':'签名已到期','unknown':'尚未完成签名'}[v['signature']])
        self.detail_status.set(v['detail']);self.detail_label.configure(foreground=ui.RED if v['tag']=='error' else ui.INK)
        can_start=not self.closing and (self.scanning or (v['ready'] and v['signature'] in ('valid','soon'))) and (not v['alive'] or v['stopping']) and not cfg.get('_load_error')
        self.start_selected_button.configure(state='normal' if can_start else 'disabled',text='停止后重启' if v['stopping'] else '检测后开始' if self.scanning else '开始此设备')
        self.stop_selected_button.configure(state='normal' if v['alive'] or udid in self.pending_starts else 'disabled')
        self.config_button.configure(state='disabled' if self.closing else 'normal');self.setup_button.configure(state='disabled' if self.closing or v['alive'] else 'normal')
    def setup_selected(self):
        udid=self.selected()
        if not udid:return
        try:self.console(udid,True).open_devices()
        except Exception as exc:self.status.set('无法打开配置：'+str(exc))
    def selected(self):
        values=self.table.selection()
        if not values:self.status.set('请先在列表选择一台设备。');return None
        return values[0]
    def console(self,udid,show=False):
        if udid not in self.panels:
            from panel import Panel
            cfg=next(c for c in self.store.profiles() if c['device_udid']==udid)
            if cfg.get('_load_error'):raise RuntimeError(cfg['_load_error'])
            window=tk.Toplevel(self.root);p=Panel(window,config=cfg,config_path=self.store.path(udid));p.pinned_device=True
            window.title(cfg.get('device_name','iPhone')+' · 配置与日志');window.protocol('WM_DELETE_WINDOW',window.withdraw)
            window.bind('<Command-q>',lambda _:self.close());self.root.createcommand('tk::mac::Quit',self.close)
            self.panels[udid]=p;window.withdraw()
        p=self.panels[udid]
        if show:p.root.deiconify();p.root.lift()
        return p
    def open_selected(self):
        udid=self.selected()
        if udid:
            try:self.console(udid,True)
            except Exception as exc:self.errors[udid]=str(exc);self.status.set('无法打开设备：'+str(exc));self.redraw()
    def start_device(self,udid,**options):
        if self.__dict__.get('scanning'):
            self.pending_starts[udid]=dict(options);self.status.set('已安排开始，连接检测完成后自动执行。');self.redraw();return
        try:
            if udid not in self.online:raise RuntimeError('设备未通过 USB 连接，请刷新连接。')
            if not self.online[udid].get('paired') or not self.online[udid].get('developer'):raise RuntimeError('USB 通信未就绪，请解锁、信任电脑并检查开发者模式后刷新。')
            p=self.console(udid);p.batch_mode=True
            try:p.start(**options)
            finally:p.batch_mode=False
            self.errors.pop(udid,None)
        except Exception as exc:self.errors[udid]='无法开始：'+str(exc).splitlines()[0][:180]
        self.redraw()
    def start_selected(self):
        udid=self.selected()
        if udid:self.start_device(udid)
    def stop_selected(self):
        udid=self.selected()
        if udid:self.stop_device(udid)
    def stop_device(self,udid):
        self.__dict__.get('pending_starts',{}).pop(udid,None)
        p=self.panels.get(udid)
        if p:p.pending_start=None;p.stop()
        self.redraw()
    def start_all(self,**options):
        if self.closing:return
        if self.scanning:
            self.pending_all=dict(options);self.status.set('已安排全部开始，连接检测完成后自动执行。');self.redraw();return
        profiles=self.store.profiles()
        if not profiles:self.status.set('请先连接手机并完成设备配置。');return
        for cfg in profiles:self.start_device(cfg['device_udid'],**options)
        self.status.set('批量开始已处理，请查看各设备状态。')
    def stop_all(self):
        self.pending_starts.clear();self.pending_all=None
        for udid in list(self.panels):
            try:self.stop_device(udid)
            except Exception as exc:self.errors[udid]='停止失败：'+str(exc)[:160]
        self.status.set('已分别发送停止请求，正在运行的设备将等待各自当前命令结束。')
    def poll(self):
        try:
            while True:
                event,value=self.events.get_nowait();self.scanning=False;self.scan_progress.stop();self.scan_progress.pack_forget();self.refresh_button.configure(state='normal',text='刷新连接')
                if event=='devices':
                    self.online={d['udid']:d for d in value}
                    for d in value:
                        try:self.store.add(d)
                        except (OSError,ValueError,KeyError):self.errors[d['udid']]='配置文件无法读取，请检查该设备 config.json。'
                    self.last_refresh=time.time();self.status.set(f'连接检测完成，{len(value)} 台 USB iPhone。各设备可独立配置与运行。')
                    pending=self.pending_starts;all_options=self.pending_all;self.pending_starts={};self.pending_all=None
                    if all_options is not None:self.start_all(**all_options)
                    else:
                        for udid,options in pending.items():self.start_device(udid,**options)
                else:
                    self.pending_starts.clear();self.pending_all=None;self.status.set('连接检测失败：'+value+'；请刷新后重试。')
        except queue.Empty:pass
        try:self.redraw()
        except Exception as exc:self.status.set('设备列表更新失败：'+str(exc)[:180])
        if self.closing:
            busy=bool(self.device_window and self.device_window.busy) or any((p.worker and p.worker.is_alive()) or (p.device_window and p.device_window.busy) for p in self.panels.values())
            if not busy:
                import runtime_manager
                runtime_manager.stop_all();self.root.destroy();return
        self.root.after(250,self.poll)
    def close(self):
        if self.__dict__.get('preview'):self.preview.close()
        self.closing=True;self.start_all_button.configure(state='disabled');self.add_button.configure(state='disabled');self.stop_all();self.status.set('正在结束各设备任务，完成后退出…')
