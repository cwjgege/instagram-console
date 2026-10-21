"""Independent, read-only screen stream. Frames stay in memory."""
import io,json,os,re,select,subprocess,tempfile,threading,time
from pathlib import Path
from PIL import Image,ImageOps,ImageTk
import tkinter as tk
from tkinter import ttk
from app_paths import ASSETS
import ui_theme as ui

MAX_FRAME=6*1024*1024
PREVIEW_RATES={'省资源 · 2 帧/秒':2,'均衡 · 5 帧/秒':5,'流畅 · 10 帧/秒':10}

class FrameParser:
    def __init__(self):self.buffer=bytearray()
    def feed(self,data):
        self.buffer.extend(data);frames=[]
        while self.buffer:
            start=self.buffer.find(b'\xff\xd8')
            if start<0:
                self.buffer[:]=self.buffer[-1:] if self.buffer[-1:]==b'\xff' else b'';break
            if start:del self.buffer[:start]
            end=self.buffer.find(b'\xff\xd9',2)
            if end<0:
                if len(self.buffer)>MAX_FRAME:self.buffer.clear()
                break
            end+=2
            if end<=MAX_FRAME:frames.append(bytes(self.buffer[:end]))
            del self.buffer[:end]
        return frames

def stream_request(udid):
    import device_manager as dm
    dm.device_dir(udid)
    import connections
    setting=connections.load(udid)
    if setting['mode']!='usb':
        if setting['mode']=='remote':url=setting.get('preview_url')
        else:
            try:url=json.loads((dm.device_dir(udid)/'network-runtime.json').read_text()).get('preview_url')
            except (OSError,ValueError):url=None
        if not url:raise RuntimeError('网络预览未配置，浏览任务可独立运行。')
        connections.validate_url(url)
        from urllib.parse import urlsplit
        u=urlsplit(url);vendor=dm.prepare_vendor()
        return vendor,{'vendor':str(vendor),'udid':udid,'port':u.port or (443 if u.scheme=='https' else 80),'host':u.hostname,'tls':u.scheme=='https'}
    try:ports=json.loads((dm.DATA/'ports.json').read_text())[udid.lower()];port=ports['mjpeg']
    except (OSError,ValueError,KeyError,TypeError):raise RuntimeError('等待控制组件就绪，开始浏览后会自动连接。')
    if type(port) is not int or not 1024<port<65536:raise RuntimeError('预览端口配置不可用，请重新检查设备配置。')
    vendor=dm.prepare_vendor()
    return vendor,{'vendor':str(vendor),'udid':udid,'port':port}

class PreviewSource:
    """One cancellable reader, one latest frame, no dependency on browser workers."""
    def __init__(self,resolver=stream_request):
        self.fps=10;self.target_size=(600,1000)
        self.resolver=resolver;self.lock=threading.Lock();self.wake=threading.Event();self.closed=False
        self.udid=None;self.paused=False;self.revision=0;self.latest=None;self.status='请选择一台设备';self.process=None
        self.thread=threading.Thread(target=self._run,daemon=True,name='screen-preview');self.thread.start()
    def set_quality(self,fps,size=None):
        if fps not in PREVIEW_RATES.values():raise ValueError('不支持的预览帧率')
        with self.lock:
            self.fps=fps
            if size:self.target_size=(max(64,min(1600,int(size[0]))),max(64,min(1600,int(size[1]))))
    def select(self,udid,paused=False):
        with self.lock:
            if (udid,paused)==(self.udid,self.paused):return self.revision
            self.udid=udid;self.paused=paused;self.revision+=1;self.latest=None
            self.status='预览已暂停' if paused else '正在连接预览…' if udid else '请选择一台设备'
            process=self.process;revision=self.revision
        if process:
            try:process.terminate()
            except ProcessLookupError:pass
        self.wake.set();return revision
    def current(self,revision):
        with self.lock:return not self.closed and self.revision==revision and self.udid and not self.paused
    def publish(self,revision,image=None,status=None):
        with self.lock:
            if self.closed or revision!=self.revision or self.paused:return False
            if image is not None:self.latest=(revision,image,time.time())
            if status is not None:self.status=status
            return True
    def snapshot(self):
        with self.lock:
            value=self.latest;self.latest=None
            return self.revision,value,self.status
    def close(self):
        with self.lock:self.closed=True;process=self.process
        if process:
            try:process.terminate()
            except ProcessLookupError:pass
        self.wake.set()
    def _run(self):
        while True:
            with self.lock:
                if self.closed:return
                udid,paused,revision=self.udid,self.paused,self.revision
            if not udid or paused:self.wake.wait(.2);self.wake.clear();continue
            try:self._stream(udid,revision)
            except Exception as exc:
                message=str(exc) if isinstance(exc,RuntimeError) else '预览连接中断，正在重试；浏览任务不受此错误控制。'
                self.publish(revision,status=message)
            if self.current(revision):self.wake.wait(3)
            self.wake.clear()
    def _stream(self,udid,revision):
        import device_manager as dm
        vendor,request=self.resolver(udid)
        if not self.current(revision):return
        with tempfile.TemporaryDirectory(prefix='ig-preview-') as folder:
            file=Path(folder)/'request.json';file.write_text(json.dumps(request))
            process=subprocess.Popen([str(vendor/'bin/node'),str(ASSETS/'resources/preview_stream.cjs'),str(file)],env=dm.child_env(vendor),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            with self.lock:
                self.process=process
                cancelled=self.closed or revision!=self.revision or self.paused
            parser=FrameParser();last_data=time.monotonic();last_emit=0
            try:
                if cancelled:return
                while self.current(revision):
                    if time.monotonic()-last_data>8:raise RuntimeError('暂未收到手机画面，正在重试。')
                    if process.poll() is not None:raise RuntimeError('预览已断开，正在重试；请确认手机已解锁、控制组件可用。')
                    if not select.select([process.stdout],[],[],.2)[0]:
                        if time.monotonic()-last_data>8:raise RuntimeError('暂未收到手机画面，正在重试。')
                        continue
                    data=os.read(process.stdout.fileno(),65536)
                    if not data:raise RuntimeError('预览已断开，正在重试。')
                    for frame in parser.feed(data)[-1:]:
                        now=time.monotonic()
                        with self.lock:fps,size=self.fps,self.target_size
                        if now-last_emit<1/fps:continue
                        try:
                            with Image.open(io.BytesIO(frame)) as raw:
                                if raw.width*raw.height>12_000_000:continue
                                raw.draft('RGB',size);raw.load();image=ImageOps.exif_transpose(raw).convert('RGB')
                                image.thumbnail(size,Image.Resampling.BILINEAR)
                        except (OSError,ValueError):continue
                        last_data=now;last_emit=now;self.publish(revision,image=image,status='实时画面 · 只读')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=1)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)
                process.stdout.close()
                with self.lock:
                    if self.process is process:self.process=None

class PreviewPane:
    def __init__(self,parent,source=None):
        self.frame=ttk.Frame(parent,width=310,padding=14);self.frame.pack_propagate(False)
        self.source=source or PreviewSource();self.selected=None;self.paused=False;self.image=None;self.frame_time=None;self.photo=None;self.zoom=None;self.closed=False;self.dirty=True
        self.title=tk.StringVar(value='手机画面');self.state=tk.StringVar(value='选择设备后显示画面');self.updated=tk.StringVar(value='尚未收到画面')
        ttk.Label(self.frame,textvariable=self.title,style='Section.TLabel').pack(anchor='w')
        ttk.Label(self.frame,text='只读预览 · 仅显示当前选中的手机',style='Muted.TLabel',wraplength=280).pack(anchor='w',pady=(5,12))
        controls=ttk.Frame(self.frame);controls.pack(fill='x',pady=(0,12))
        self.pause_button=ttk.Button(controls,text='暂停预览',command=self.toggle,state='disabled');self.pause_button.pack(side='left')
        self.zoom_button=ttk.Button(controls,text='放大',command=self.enlarge,state='disabled');self.zoom_button.pack(side='right')
        quality=ttk.Frame(self.frame);quality.pack(fill='x',pady=(0,10))
        ttk.Label(quality,text='预览帧率').pack(side='left')
        self.quality=tk.StringVar(value='流畅 · 10 帧/秒')
        self.quality_picker=ttk.Combobox(quality,textvariable=self.quality,values=list(PREVIEW_RATES),state='readonly',width=17)
        self.quality_picker.pack(side='right');self.quality_picker.bind('<<ComboboxSelected>>',self.change_quality)
        ttk.Label(self.frame,text='随时切换；帧率越高，电脑开销越大。实际帧率受手机和连接质量影响。',style='Muted.TLabel',wraplength=280).pack(anchor='w',pady=(0,10))
        footer=ttk.Frame(self.frame);footer.pack(side='bottom',fill='x',pady=(10,0))
        self.state_label=ttk.Label(footer,textvariable=self.state,wraplength=280);self.state_label.pack(fill='x')
        ttk.Label(footer,textvariable=self.updated,style='Muted.TLabel',wraplength=280).pack(fill='x',pady=(5,0))
        ttk.Label(self.frame,text='⌘⇧P 暂停 / 继续    ⌘⇧O 放大',style='Muted.TLabel',font=('PingFang SC',10),wraplength=280).pack(anchor='w',pady=(0,8))
        root=self.frame.winfo_toplevel()
        root.bind('<Command-Shift-P>',lambda _:self.toggle(),add='+');root.bind('<Command-Shift-O>',lambda _:self.enlarge(),add='+')
        self.canvas=tk.Canvas(self.frame,background='#111820',highlightthickness=0,borderwidth=0);self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda _:self.invalidate())
        self.frame.after(100,self.poll)
    def select(self,udid,name='手机画面'):
        title=name if udid else '手机画面'
        if self.title.get()!=title:self.title.set(title)
        if udid==self.selected:return
        self.selected=udid;self.image=None;self.frame_time=None;self.photo=None
        self.source.select(udid,self.paused);self.pause_button.configure(state='normal' if udid else 'disabled');self.zoom_button.configure(state='disabled')
        self.state.set('预览已暂停' if self.paused else '正在连接预览…' if udid else '选择设备后显示画面')
        self.updated.set('尚未收到画面');self.invalidate();self.paint(self.canvas)
        if self.zoom and self.zoom.winfo_exists():self.paint(self.zoom_canvas,True)
    def toggle(self):
        if not self.selected:return
        self.paused=not self.paused;self.source.select(self.selected,self.paused)
        self.pause_button.configure(text='继续预览' if self.paused else '暂停预览')
        self.state.set('已暂停预览；暂停仅影响画面' if self.paused else '正在重新连接预览…');self.invalidate()
    def change_quality(self,event=None):
        self.source.set_quality(PREVIEW_RATES[self.quality.get()]);self.invalidate()
    def invalidate(self):self.dirty=True
    def enlarge(self):
        if self.zoom and self.zoom.winfo_exists():self.zoom.lift();return
        if self.image is None:return
        root=self.frame.winfo_toplevel();self.zoom=tk.Toplevel(root);self.zoom.title('手机画面 · 只读放大')
        self.zoom.geometry(f'520x{min(900,root.winfo_screenheight()-90)}');self.zoom.minsize(320,480)
        ttk.Label(self.zoom,textvariable=self.title,style='Section.TLabel',padding=12).pack(fill='x')
        ttk.Label(self.zoom,textvariable=self.updated,style='Muted.TLabel',padding=(12,0,12,8)).pack(fill='x')
        zoom_state=ttk.Label(self.zoom,textvariable=self.state,padding=12,wraplength=460)
        zoom_state.pack(side='bottom',fill='x')
        self.zoom.bind('<Configure>',lambda e:zoom_state.configure(wraplength=max(260,e.width-24)) if e.widget==self.zoom else None,add='+')
        self.zoom_canvas=tk.Canvas(self.zoom,background='#111820',highlightthickness=0);self.zoom_canvas.pack(fill='both',expand=True)
        self.zoom_canvas.bind('<Configure>',lambda _:self.invalidate());self.zoom.bind('<Escape>',lambda _:self.close_zoom());self.zoom.bind('<Command-Shift-P>',lambda _:self.toggle())
        self.zoom.protocol('WM_DELETE_WINDOW',self.close_zoom);self.invalidate()
    def close_zoom(self):
        if self.zoom:self.zoom.destroy();self.zoom=None
        self.zoom_photo=None
    def paint(self,canvas,zoom=False):
        canvas.delete('all');w=canvas.winfo_width();h=canvas.winfo_height()
        if w<4 or h<4:return
        if self.image is None:
            canvas.create_text(w/2,h/2,text='预览已暂停' if self.paused else '等待手机画面',fill='#D5DEE8',font=('PingFang SC',12),width=max(120,w-30));return
        image=ImageOps.contain(self.image,(w,h),Image.Resampling.BILINEAR);photo=ImageTk.PhotoImage(image,master=canvas)
        if zoom:self.zoom_photo=photo
        else:self.photo=photo
        canvas.create_image(w/2,h/2,image=photo)
        self.update_badge(canvas)
    def update_badge(self,canvas):
        if self.image is None or self.frame_time is None:return
        age=max(0,int(time.time()-self.frame_time))
        if self.paused or age>=5:
            if not canvas.find_withtag('stale'):
                canvas.create_rectangle(0,0,canvas.winfo_width(),30,fill='#263442',outline='',tags=('stale','stale-bg'))
                canvas.create_text(10,15,anchor='w',fill='white',font=('PingFang SC',10),tags=('stale','stale-label'))
            canvas.itemconfigure('stale-label',text=('已暂停 · ' if self.paused else '最后画面 · ')+f'{age} 秒前')
        else:canvas.delete('stale')
    def poll(self):
        if self.closed:return
        try:self.update_frame()
        except Exception:
            self.state.set('预览显示异常，正在恢复；浏览任务独立运行。');self.dirty=True
        if not self.closed:self.frame.after(50,self.poll)
    def update_frame(self):
        if hasattr(self,'quality'):
            size=(self.canvas.winfo_width(),self.canvas.winfo_height())
            if self.zoom and self.zoom.winfo_exists():size=(max(size[0],self.zoom_canvas.winfo_width()),max(size[1],self.zoom_canvas.winfo_height()))
            self.source.set_quality(PREVIEW_RATES[self.quality.get()],size)
        revision,packet,status=self.source.snapshot()
        if packet and packet[0]==revision and not self.paused:
            _,self.image,self.frame_time=packet;self.state.set(status);self.zoom_button.configure(state='normal');self.dirty=True
        elif not self.paused:self.state.set(status)
        if self.frame_time:
            age=max(0,int(time.time()-self.frame_time));stamp=time.strftime('%H:%M:%S',time.localtime(self.frame_time))
            self.updated.set(f'最后更新 {stamp} · {age} 秒前')
            if age>=5 and not self.paused and status.startswith('实时画面'):self.state.set('画面暂未更新，等待恢复连接…')
        if self.dirty:
            self.paint(self.canvas)
            if self.zoom and self.zoom.winfo_exists():self.paint(self.zoom_canvas,True)
            self.dirty=False
        else:
            self.update_badge(self.canvas)
            if self.zoom and self.zoom.winfo_exists():self.update_badge(self.zoom_canvas)
    def close(self):
        if self.closed:return
        self.closed=True;self.source.close();self.close_zoom()
