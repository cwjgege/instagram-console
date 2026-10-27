"""Native multi-device overview; each device retains its own complete console."""
import copy,json,queue,threading,time,subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from app_paths import DATA,APP_VERSION,load_config
import device_manager as dm
import ui_theme as ui
import connections

from device_store import DeviceStore,display_name,normalized

class Fleet:
    def __init__(self,root,store=None):
        self.root=root;self.store=store or DeviceStore();self.panels={};self.online={};self.errors={};self.events=queue.Queue();self.scanning=False;self.closing=False;self.device_window=None
        self.cfg=copy.deepcopy(self.store.template);self.cfg['device_udid']='';self.worker=None
        self.on_device_selected=self.selected_device_ready;self.signature_notices=set()
        self.pending_starts={};self.pending_all=None;self.last_refresh=None;self._view_cache={}
        from location_manager import LocationManager
        self.locations=LocationManager();self.location_windows={};self.location_shutdown=False
        self.usb_ids=set();self.usb_checking=False;self.next_usb_check=0;self.usb_refresh_pending=False
        self.next_network_check=time.monotonic()+15
        ui.apply(root);root.title(f'Instagram 控制台 {APP_VERSION} · 设备工作区')
        root.geometry(f'{min(1260,root.winfo_screenwidth()-60)}x{min(780,root.winfo_screenheight()-100)}');root.minsize(1000,640)
        body=ttk.Frame(root,padding=24,style='Workspace.TFrame');body.pack(fill='both',expand=True)
        header=ttk.Frame(body,style='Workspace.TFrame');header.pack(fill='x',pady=(0,18))
        title=ttk.Frame(header,style='Workspace.TFrame');title.pack(side='left')
        ttk.Label(title,text='设备工作区',font=('PingFang SC',22,'bold'),background=ui.BG,foreground=ui.INK).pack(anchor='w')
        ui.version_label(title,workspace=True)
        self.summary=tk.StringVar(value='正在检测设备连接…')
        ttk.Label(title,textvariable=self.summary,style='Workspace.TLabel').pack(anchor='w',pady=(5,0))
        self.add_button=ttk.Button(header,text='添加设备',command=self.open_devices);self.add_button.pack(side='right',anchor='n')
        self.add_network_button=ttk.Button(header,text='添加无线设备',command=self.add_network);self.add_network_button.pack(side='right',padx=8,anchor='n')
        workspace=ttk.Frame(body,style='Workspace.TFrame');workspace.pack(fill='both',expand=True)
        from preview import PreviewPane
        self.preview=PreviewPane(workspace);self.preview.frame.pack(side='right',fill='y',padx=(16,0))
        sheet=ttk.Frame(workspace,padding=18);sheet.pack(side='left',fill='both',expand=True)
        toolbar=ttk.Frame(sheet);toolbar.pack(fill='x',pady=(0,14))
        ttk.Label(toolbar,text='已配置设备',style='Section.TLabel').pack(side='left')
        self.refresh_button=ttk.Button(toolbar,text='刷新连接',command=self.refresh);self.refresh_button.pack(side='left',padx=12)
        self.manage_button=ttk.Menubutton(toolbar,text='设备管理')
        self.manage_menu=tk.Menu(self.manage_button,tearoff=False)
        self.manage_menu.add_command(label='重命名…',command=self.rename_selected)
        self.manage_menu.add_command(label='删除设备…',command=self.delete_selected)
        self.manage_button.configure(menu=self.manage_menu);self.manage_button.pack(side='left')
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
        self.meta_label=ttk.Label(detail,textvariable=self.detail_meta,style='Muted.TLabel',wraplength=640);self.meta_label.pack(anchor='w',pady=(4,7))
        self.detail_label=ttk.Label(detail,textvariable=self.detail_status,wraplength=920);self.detail_label.pack(fill='x',pady=(0,12))
        actions=ttk.Frame(detail);actions.pack(fill='x')
        self.start_selected_button=ttk.Button(actions,text='开始此设备',command=self.start_selected,style='Primary.TButton');self.start_selected_button.pack(side='left')
        self.stop_selected_button=ttk.Button(actions,text='停止此设备',command=self.stop_selected);self.stop_selected_button.pack(side='left',padx=8)
        handoff=ttk.Frame(detail);handoff.pack(fill='x',pady=(8,0))
        self.pause_selected_button=ttk.Button(handoff,text='暂停托管',command=lambda:self.hosting_action('pause_hosting'));self.pause_selected_button.pack(side='left')
        self.resume_selected_button=ttk.Button(handoff,text='继续托管',command=lambda:self.hosting_action('resume_hosting'));self.resume_selected_button.pack(side='left',padx=8)
        self.location_button=ttk.Button(handoff,text='固定定位',command=self.open_location);self.location_button.pack(side='right')
        self.config_button=ttk.Button(actions,text='配置与日志',command=self.open_selected);self.config_button.pack(side='left')
        self.setup_button=ttk.Button(actions,text='连接与签名',command=self.setup_selected);self.setup_button.pack(side='right')
        self.connection_button=ttk.Button(actions,text='连接方式',command=self.open_connection);self.connection_button.pack(side='left',padx=8)
        footer=ttk.Frame(body,style='Workspace.TFrame');footer.pack(side='bottom',fill='x',pady=(12,0),before=workspace)
        self.status=tk.StringVar(value='连接检测通常需要几秒，期间可以安排开始。')
        self.status_label=ttk.Label(footer,textvariable=self.status,wraplength=850,style='Workspace.TLabel');self.status_label.pack(fill='x')
        ttk.Label(footer,text='⌘ Return 全部开始    Esc 全部停止    ⌘ L 固定定位    双击设备打开配置',style='Workspace.TLabel',font=('PingFang SC',10)).pack(anchor='w',pady=(6,0))
        self.device_label=tk.StringVar();root.protocol('WM_DELETE_WINDOW',self.close);root.createcommand('tk::mac::Quit',self.close)
        root.bind('<Escape>',lambda _:self.stop_all());root.bind('<Command-Return>',lambda _:self.start_all())
        root.bind('<Command-k>',lambda _:self.add_network())
        root.bind('<Command-l>',lambda _:self.open_location())
        root.bind('<Configure>',lambda event:self.resize(event),add='+')
        root.bind('<Destroy>',lambda e:self.preview.close() if e.widget==root else None,add='+')
        self.update_detail();root.after(100,self.refresh);root.after(200,self.poll)
    def resize(self,event):
        if event.widget!=self.root:return
        available=max(480,event.width-410)
        self.meta_label.configure(wraplength=available);self.detail_label.configure(wraplength=available);self.status_label.configure(wraplength=max(650,event.width-60))
        self.table.configure(displaycolumns=('device','state','progress') if event.width<1150 else ('device','system','connection','state','progress'))
    def save_settings(self,quiet=False):return True
    def update_signature(self):pass
    def selected_device_ready(self,device):
        self.store.add(device,explicit=True);self.errors.pop(device['udid'],None);self.redraw();self.status.set('设备已添加：'+device['name'])
    def open_devices(self):
        if self.device_window:self.device_window.window.lift();return
        from device_ui import DeviceWindow
        self.device_window=DeviceWindow(self)
    def refresh(self):
        if self.scanning or self.closing:return
        self.scanning=True;self.refresh_button.configure(state='disabled',text='正在检测…');self.scan_progress.pack(fill='x',pady=(0,8),before=self.table_area);self.scan_progress.start(15);self.status.set('正在检测设备连接，可安排检测完成后开始。');self.redraw()
        def scan():
            try:self.events.put(('devices',connections.fleet_devices(self.store.profiles())))
            except Exception as exc:self.events.put(('error',str(exc)))
        threading.Thread(target=scan,daemon=True).start()
    def refresh_remote_health(self):
        if self.closing or self.scanning or self.__dict__.get('network_scanning'):return
        self.network_scanning=True
        profiles=self.store.profiles();started=time.time()
        def check():
            try:self.events.put(('remote_devices',(started,connections.probe_remotes(profiles))))
            except Exception:self.events.put(('remote_devices',(started,[])))
        threading.Thread(target=check,daemon=True).start()
    def device_view(self,cfg):
        udid=cfg['device_udid'];p=self.panels.get(udid);record=dm.signing_record(udid)
        alive=bool(p and p.worker and p.worker.is_alive());stopping=bool(alive and p.stop_event.is_set())
        online=self.online.get(udid);ready=bool(online and online.get('paired') and online.get('developer'))
        detail=self.errors.get(udid) or cfg.get('_load_error') or (p.status.get() if p else '')
        signature,_=dm.expiry_state(record)
        connection_error=''
        try:mode=connections.load(udid)['mode'] if len(udid)>=10 else 'usb'
        except ValueError as exc:mode='usb';connection_error=str(exc);ready=False;detail=connection_error
        if mode=='remote':signature='external'
        if online and online.get('connection_error'):
            connection_error=online['connection_error'];detail='控制连接检查失败：'+connection_error+'\n实时预览与控制端口分别检测；有画面不代表控制接口已就绪。'
        setup=dm.setup_status(udid) if len(udid)>=10 else None
        if mode!='remote' and setup and setup.get('status')!='ready':detail=setup.get('message') or detail
        if udid in self.__dict__.get('pending_starts',{}) or self.__dict__.get('pending_all') is not None:state,tag='等待连接检测','pending'
        elif self.scanning and not online:state,tag='检测连接中','pending'
        elif stopping:state,tag='正在停止','pending'
        elif alive:
            search=getattr(getattr(p,'runner',None),'feature_state',{}).get('search',{})
            if search.get('cooldown'):
                state,tag='浏览中 · 搜索冷却','running';detail+='\n'+search.get('reason','等待下一次搜索')
            elif not search.get('enabled',True) and search.get('reason'):
                state,tag='浏览中 · 搜索暂停','pending';detail+='\n搜索已暂停：'+search['reason']
            else:state,tag='运行中','running'
            pause_state=getattr(getattr(p,'runner',None),'pause_state','running')
            if pause_state in ('pausing','paused','resuming'):state,tag={'pausing':'正在暂停托管','paused':'已暂停托管','resuming':'重新识别页面中'}[pause_state],'pending'
        elif connection_error:state,tag='控制连接异常' if online else '连接配置异常','error'
        elif self.errors.get(udid) or cfg.get('_load_error') or (detail.startswith('已停止：')):state,tag='需要处理','error'
        elif not online:state,tag='设备未连接','pending'
        elif not ready:state,tag='检查连接','pending'
        elif mode!='remote' and setup and setup.get('status')!='ready':state,tag='等待手机信任' if setup.get('status')=='awaiting_trust' else '配置未完成','pending'
        elif signature in ('unknown','expired'):state,tag='待配置' if signature=='unknown' else '签名已到期','pending'
        elif signature=='soon':state,tag='签名即将到期','pending'
        else:state,tag='已就绪','idle'
        if not detail:
            detail=('检测完成后将自动开始。' if self.scanning else '请连接这台手机并刷新连接。') if not online else ('请打开“连接与签名”完成解锁、信任或签名配置。' if tag=='pending' else '可以开始浏览，或打开配置调整此设备的设置。')
        if self.scanning and not online:detail='已安排开始，连接检测完成后自动执行。' if self.pending_all is not None or udid in self.pending_starts else '正在检测此设备的连接，请稍候。'
        if not alive and not online and not self.scanning:detail='请连接这台手机并刷新连接。'+(' '+detail if self.errors.get(udid) else '')
        return dict(mode=mode,state=state,tag=tag,detail=detail,alive=alive,stopping=stopping,ready=ready,signature=signature,panel=p)
    def redraw(self):
        profiles=self.store.profiles();profiles.sort(key=lambda c:normalized(c['device_udid']) not in self.__dict__.get('usb_ids',set()));self._view_cache={c['device_udid']:(c,self.device_view(c)) for c in profiles};ids=set(self._view_cache)
        for udid in self.table.get_children():
            if udid not in ids:self.table.delete(udid)
        running=attention=0
        for udid,(cfg,v) in self._view_cache.items():
            p=v['panel'];running+=v['alive'];attention+=v['tag'] in ('error','pending')
            stats=f'翻页 {p.runner.swipes}   ·   搜索 {p.runner.searches}' if p and hasattr(p,'runner') else '尚未运行'
            values=(display_name(cfg)+('  [USB 已插入]' if normalized(udid) in self.__dict__.get('usb_ids',set()) else ''),'iOS '+cfg.get('device_ios','未知'),{'usb':'USB','wifi':'Wi-Fi','remote':'网络 WDA'}[v['mode']] if v['ready'] else '检测中' if self.scanning and udid not in self.online else '待检查' if udid in self.online else '离线',v['state'],stats)
            if self.table.exists(udid):
                if tuple(self.table.item(udid,'values'))!=values:self.table.item(udid,values=values,tags=(v['tag'],))
            else:self.table.insert('', 'end',iid=udid,values=values,tags=(v['tag'],))
        for index,cfg in enumerate(profiles):self.table.move(cfg['device_udid'],'',index)
        if profiles:
            self.detail_frame.pack(side='bottom',fill='x',before=self.table_area)
            self.empty.pack_forget();self.table.pack(fill='both',expand=True)
            if not self.table.selection():self.table.selection_set(profiles[0]['device_udid'])
        else:
            self.detail_frame.pack_forget();self.table.pack_forget();self.empty.pack(fill='both',expand=True)
        self.summary.set(f'正在检测连接   ·   已保存 {len(profiles)} 台设备' if self.scanning else f'{len(profiles)} 台设备   ·   {running} 台运行中   ·   {attention} 台需处理' if profiles else '首次安装使用 USB；已配置手机可选择无线连接')
        self.start_all_button.configure(state='disabled' if self.closing or not profiles else 'normal',text='检测后全部开始' if self.scanning else '全部开始')
        waiting=bool(self.pending_starts) or self.pending_all is not None
        self.stop_all_button.configure(state='normal' if running or waiting else 'disabled')
        self.update_detail()
    def update_detail(self):
        selected=self.table.selection();udid=selected[0] if selected else None
        item=self._view_cache.get(udid)
        if hasattr(self,'manage_button'):self.manage_button.configure(state='normal' if item and not self.closing else 'disabled')
        pause_state=getattr(getattr(item[1]['panel'],'runner',None),'pause_state','running') if item else 'running'
        active=bool(item and item[1]['alive'] and not item[1]['stopping'])
        self.pause_selected_button.configure(state='normal' if active and pause_state=='running' else 'disabled')
        self.resume_selected_button.configure(state='normal' if active and pause_state=='paused' else 'disabled')
        if self.__dict__.get('preview'):self.preview.select(udid,item[0].get('device_name','iPhone') if item else '手机画面')
        if not item:
            self.detail_title.set('尚未选择设备');self.detail_meta.set('');self.detail_status.set('请选择要操作的设备。')
            for button in (self.start_selected_button,self.stop_selected_button,self.config_button,self.setup_button,self.connection_button,self.location_button):button.configure(state='disabled')
            return
        self.location_button.configure(state='disabled' if self.closing else 'normal')
        self.connection_button.configure(state='disabled' if self.closing or item[1]['alive'] else 'normal')
        cfg,v=item;self.detail_title.set(display_name(cfg))
        self.detail_meta.set('iOS '+cfg.get('device_ios','未知')+'   ·   当前控制：'+(connections.MODES[v['mode']]+' 已就绪' if v['ready'] else '连接检测中' if self.scanning else connections.MODES[v['mode']]+' 未就绪')+'   ·   '+{'valid':'签名有效','soon':'签名即将到期','expired':'签名已到期','unknown':'尚未完成签名','external':'远端签名需当地维护'}[v['signature']])
        identity='原名：'+cfg.get('device_original_name',cfg.get('device_name','iPhone'))+'  ·  编号末尾 '+udid[-8:]
        physical='USB 已插入' if normalized(udid) in self.__dict__.get('usb_ids',set()) else 'USB 未插入'
        self.detail_meta.set(identity+'\n'+physical+'  ·  '+self.detail_meta.get())
        self.detail_status.set(v['detail']);self.detail_label.configure(foreground=ui.RED if v['tag']=='error' else ui.INK)
        can_start=not self.closing and (self.scanning or (v['ready'] and v['signature'] in ('valid','soon','external'))) and (not v['alive'] or v['stopping']) and not cfg.get('_load_error')
        self.start_selected_button.configure(state='normal' if can_start else 'disabled',text='停止后重启' if v['stopping'] else '检测后开始' if self.scanning else '开始此设备')
        self.stop_selected_button.configure(state='normal' if v['alive'] or udid in self.pending_starts else 'disabled')
        self.config_button.configure(state='disabled' if self.closing else 'normal');self.setup_button.configure(state='disabled' if self.closing or v['alive'] else 'normal')
    def check_usb(self):
        self.usb_checking=True
        def check():
            try:value=dm.usb_serials()
            except Exception:value=None
            self.events.put(('usb_devices',value))
        threading.Thread(target=check,daemon=True,name='usb-presence').start()

    def rename_selected(self):
        udid=self.selected()
        if not udid or self.closing:return
        item=self._view_cache.get(udid)
        if not item:return
        name=simpledialog.askstring('重命名设备','软件内显示名称（1～40 个字符）：',initialvalue=display_name(item[0]),parent=self.root)
        if name is None:return
        try:
            cfg=self.store.rename(udid,name);name=display_name(cfg)
            panel=self.panels.get(udid)
            if panel:
                for key in ('device_name','device_alias','device_original_name'):panel.cfg[key]=cfg[key]
                panel.title_name.set(name);panel.root.title(ui.version_title(name+' · 配置与日志'))
                if getattr(panel,'task_alerts',None):panel.task_alerts.device=name
            window=self.location_windows.get(udid)
            if window:window.update_identity(cfg)
            self.redraw();self.status.set('设备已重命名：'+name)
        except (OSError,ValueError) as exc:self.status.set('重命名失败：'+str(exc))

    def removal_blocker(self,udid):
        p=self.panels.get(udid)
        if p and p.worker and p.worker.is_alive():return '请先停止此设备的任务，并等待停止完成。'
        if udid in self.pending_starts or self.pending_all is not None:return '此设备已安排开始，请先取消等待开始的任务。'
        if self.device_window and (self.device_window.busy or self.device_window.wireless_running):return '设备向导正在配置，请等待配置完成。'
        if p and p.device_window and (p.device_window.busy or p.device_window.wireless_running):return '此设备正在安装或验证，请等待完成。'
        if any(w.busy for w in self.__dict__.get('connection_windows',[])):return '正在验证或导入连接，请等待完成。'
        window=self.location_windows.get(udid)
        if window and window.permission_busy:return '此设备正在请求定位授权，请等待完成。'
        state=self.locations.snapshot(udid)
        if state.get('phase')=='busy' or state.get('guard_enabled'):return '请先关闭自动保持，并恢复真实定位，等待定位操作完成。'
        with self.locations.lock:
            session=self.locations.sessions.get(udid)
            if session:return '请先在固定定位中恢复真实定位，等待定位连接结束。'
        return None

    def delete_selected(self):
        udid=self.selected()
        if not udid or self.closing:return
        blocked=self.removal_blocker(udid)
        if blocked:self.status.set('无法删除：'+blocked);return
        cfg=self._view_cache[udid][0]
        prompt=(display_name(cfg)+' · 编号末尾 '+udid[-8:]+
                '\n将清除此设备在本机的配置、连接绑定、签名记录、编译缓存和日志，并移至系统废纸篓。'+
                '\n不会卸载手机组件或删除共享证书。再次手动添加时需要重新配置。')
        if not messagebox.askokcancel('删除设备',prompt,icon='warning',parent=self.root):return
        blocked=self.removal_blocker(udid)
        if blocked:self.status.set('无法删除：'+blocked);return
        try:
            # Pause preview first so its resolver cannot recreate this directory.
            self.preview.select(None,'手机画面')
            import runtime_manager
            from app_paths import lock_device
            from contextlib import ExitStack
            with ExitStack() as locks:
                for target in sorted(self.store._related_ids(udid)):locks.enter_context(lock_device(target))
                ids=self.store.remove(udid,lambda targets:[runtime_manager.stop_device(i) for i in targets])
            for target in ids:
                panel=self.panels.pop(target,None)
                if panel:
                    panel.closing=True
                    if panel.device_window:panel.device_window.window.destroy()
                    panel.root.destroy()
                window=self.location_windows.pop(target,None)
                if window:window.close()
                self.online.pop(target,None);self.errors.pop(target,None);self.pending_starts.pop(target,None)
                with self.locations.lock:self.locations.states.pop(target,None)
            self.redraw();self.status.set('设备已删除，本地资料已移至系统废纸篓；可通过“添加设备”重新配置。')
        except Exception as exc:self.redraw();self.status.set('删除失败，设备资料已保留：'+str(exc))

    def open_location(self):
        if self.closing:return
        udid=self.selected()
        if not udid:return
        if udid in self.location_windows:self.location_windows[udid].window.lift();return
        from location_ui import LocationWindow
        cfg=next((c for c in self.store.profiles() if c['device_udid']==udid),None)
        if cfg:self.location_windows[udid]=LocationWindow(self,cfg)

    def add_network(self):
        if self.closing:return
        from connection_ui import ConnectionWindow
        try:ConnectionWindow(self)
        except Exception as exc:self.status.set('无法打开连接配置：'+str(exc))

    def open_connection(self):
        if self.closing:return
        udid=self.selected()
        if udid:
            from connection_ui import ConnectionWindow
            try:ConnectionWindow(self,udid)
            except Exception as exc:self.status.set('无法打开连接配置：'+str(exc))

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
            window.title(ui.version_title(display_name(cfg)+' · 配置与日志'));window.protocol('WM_DELETE_WINDOW',window.withdraw)
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
            if udid not in self.online:raise RuntimeError('所选连接方式未就绪，请刷新连接或检查“连接方式”。')
            if not self.online[udid].get('paired') or not self.online[udid].get('developer'):raise RuntimeError('所选设备的开发通信未就绪，请检查配对和开发者模式后刷新。')
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
    def hosting_action(self,method):
        udid=self.selected();panel=self.panels.get(udid)
        if panel:getattr(panel,method)();self.redraw()

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
                event,value=self.events.get_nowait()
                if event=='usb_devices':
                    self.usb_checking=False;self.next_usb_check=time.monotonic()+3
                    if value is not None and value!=self.usb_ids:
                        self.usb_ids=value
                        if self.scanning:self.usb_refresh_pending=True
                        else:self.refresh()
                    continue
                if event=='remote_devices':
                    self.network_scanning=False;self.next_network_check=time.monotonic()+15
                    started,rows=value
                    if self.scanning or started<(self.last_refresh or 0):continue
                    changed=any(self.online.get(d['udid'])!=d for d in rows)
                    for d in rows:
                        if not self.store.is_removed(d['udid']):self.online[d['udid']]=d
                    if changed:
                        ready_count=sum(bool(d.get('paired') and d.get('developer')) for d in self.online.values())
                        self.status.set(f'控制连接状态已更新，{ready_count} 台可连接 iPhone。')
                    continue
                self.scanning=False;self.scan_progress.stop();self.scan_progress.pack_forget();self.refresh_button.configure(state='normal',text='刷新连接')
                if event=='devices':
                    value=[d for d in value if not self.store.is_removed(d['udid'])]
                    self.online={d['udid']:d for d in value}
                    for d in value:
                        try:self.store.add(d)
                        except (OSError,ValueError,KeyError):self.errors[d['udid']]='配置文件无法读取，请检查该设备 config.json。'
                    ready_count=sum(bool(d.get('paired') and d.get('developer')) for d in value)
                    self.last_refresh=time.time();self.status.set(f'连接检测完成，{ready_count} 台可连接 iPhone。各设备可独立配置与运行。')
                    pending=self.pending_starts;all_options=self.pending_all;self.pending_starts={};self.pending_all=None
                    if all_options is not None:self.start_all(**all_options)
                    else:
                        for udid,options in pending.items():self.start_device(udid,**options)
                else:
                    self.pending_starts.clear();self.pending_all=None;self.status.set('连接检测失败：'+value+'；请刷新后重试。')
        except queue.Empty:pass
        except (OSError,ValueError) as exc:
            self.pending_starts.clear();self.pending_all=None;self.status.set('设备记录读取失败，未自动添加或启动：'+str(exc))
        if not self.closing and self.usb_refresh_pending and not self.scanning:
            self.usb_refresh_pending=False;self.refresh()
        if not self.closing and not self.usb_checking and time.monotonic()>=self.next_usb_check:self.check_usb()
        if not self.closing and time.monotonic()>=self.__dict__.get('next_network_check',float('inf')):
            self.next_network_check=time.monotonic()+15;self.refresh_remote_health()
        try:self.redraw()
        except Exception as exc:self.status.set('设备列表更新失败：'+str(exc)[:180])
        if self.closing:
            busy=bool(self.device_window and self.device_window.busy) or any((p.worker and p.worker.is_alive()) or (p.device_window and p.device_window.busy) for p in self.panels.values())
            busy=busy or any(w.busy for w in self.__dict__.get('connection_windows',[]))
            busy=busy or self.__dict__.get('location_shutdown',False)
            if not busy:
                import runtime_manager
                runtime_manager.stop_all();self.root.destroy();return
        self.root.after(250,self.poll)
    def close(self):
        if self.closing:return
        self.location_shutdown=True
        def restore_locations():
            try:self.locations.shutdown()
            finally:self.location_shutdown=False
        threading.Thread(target=restore_locations,daemon=True).start()
        for window in self.__dict__.get('connection_windows',[]):window.stop_event.set()
        if self.__dict__.get('preview'):self.preview.close()
        self.closing=True;self.start_all_button.configure(state='disabled');self.add_button.configure(state='disabled');self.stop_all();self.status.set('正在结束各设备任务，完成后退出…')
