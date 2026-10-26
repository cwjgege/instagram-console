"""User-triggered place search and visible map tiles; never changes a phone."""
import hashlib,io,json,math,queue,ssl,threading,time,urllib.parse,urllib.request,webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from PIL import Image,ImageTk
import certifi
from app_paths import DATA,APP_VERSION
import ui_theme as ui
from location_manager import coordinates,NEW_YORK

UA=f'InstagramConsole/{APP_VERSION} (+https://github.com/cwjgege/instagram-console)'
_SEARCH_LOCK=threading.Lock();_LAST_SEARCH=0
_TILE_POOL=ThreadPoolExecutor(max_workers=2,thread_name_prefix='map-tile')
_CACHE_LOCK=threading.Lock()
def project(lat,lon,zoom):
    lat,lon=coordinates(lat,lon);lat=max(-85.05112878,min(85.05112878,lat));size=256*2**zoom
    return (lon+180)/360*size,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*size
def unproject(x,y,zoom):
    size=256*2**zoom;y=max(0,min(size,y))
    return math.degrees(math.atan(math.sinh(math.pi*(1-2*y/size)))),(x/size*360)%360-180

def provider():
    defaults={'tiles':'https://tile.openstreetmap.org/{z}/{x}/{y}.png','search':'https://photon.komoot.io/api/'}
    try:custom=json.loads((DATA/'map-provider.json').read_text())
    except (OSError,ValueError):custom={}
    for key in defaults:
        if key in custom:
            value=str(custom[key]);url=urllib.parse.urlsplit(value)
            if url.scheme!='https' or not url.netloc:raise ValueError('地图服务地址必须使用有效的 HTTPS 地址。')
            defaults[key]=value
    return defaults

def download(url,limit):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=15,context=ssl.create_default_context(cafile=certifi.where())) as response:
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('地图服务返回数据过大。')
    return data

def cached(url,kind,ttl,limit):
    key=hashlib.sha256(url.encode()).hexdigest();path=DATA/'map-cache'/kind/(key+'.cache')
    # Serialize file access, not network. Only requested visible tiles are downloaded.
    with _CACHE_LOCK:
        if path.exists() and time.time()-path.stat().st_mtime<ttl:
            data=path.read_bytes()
            if len(data)<=limit:return data
    data=download(url,limit)
    with _CACHE_LOCK:
        path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_bytes(data);temp.replace(path)
    return data

def search_places(query):
    global _LAST_SEARCH
    query=query.strip()
    if not query or len(query)>160:raise ValueError('请输入 1～160 个字符的地点名称。')
    # Explicit Search/Return only; no autocomplete or bulk geocoding.
    base=provider()['search'];url=base+('&' if '?' in base else '?')+urllib.parse.urlencode({'q':query,'limit':6})
    with _SEARCH_LOCK:
        delay=1.1-(time.monotonic()-_LAST_SEARCH)
        if delay>0:time.sleep(delay)
        try:data=json.loads(cached(url,'search',86400,1024*1024))
        finally:_LAST_SEARCH=time.monotonic()
    results=[]
    for feature in data.get('features',[])[:6]:
        try:
            lon,lat=feature['geometry']['coordinates'];lat,lon=coordinates(lat,lon)
            p=feature['properties'];parts=[]
            for key in ('name','street','city','state','country'):
                item=str(p.get(key,'')).strip()
                if item and item not in parts:parts.append(item)
            label=' · '.join(parts) or f'{lat:.6f}, {lon:.6f}'
            kind=p.get('type') or p.get('osm_value')
            if kind:
                category={'city':'城市','state':'州／省','county':'县／郡','town':'城镇','village':'村庄','district':'行政区'}.get(kind,kind)
                label=f'[{category}] '+label
            results.append({'label':label,'latitude':lat,'longitude':lon})
        except (KeyError,TypeError,ValueError):continue
    return results

def tile_data(z,x,y):
    n=2**z
    if not 0<=y<n:return None
    url=provider()['tiles'].format(z=z,x=x%n,y=y)
    return cached(url,'tiles',7*86400,2*1024*1024)

class MapWindow:
    def __init__(self,parent,lat,lon,on_select,on_close=None):
        self.window=tk.Toplevel(parent);self.window.title('地图搜索与选点');self.window.geometry('850x710');self.window.minsize(640,560)
        self.closed=False;self.on_select=on_select;self.on_close=on_close;self.events=queue.Queue();self.images={};self.pending=set();self.failed=set();self.results=[];self.searching=False;self.generation=0
        try:self.lat,self.lon=coordinates(lat,lon)
        except ValueError:self.lat,self.lon=NEW_YORK
        self.selected=(self.lat,self.lon);self.zoom=12;self.drag_start=None;self.draw_token=None
        w=self.window;w.protocol('WM_DELETE_WINDOW',self.close)
        body=ttk.Frame(w,padding=16);body.pack(fill='both',expand=True)
        bar=ttk.Frame(body);bar.pack(fill='x')
        self.query=tk.StringVar();entry=ttk.Entry(bar,textvariable=self.query);entry.pack(side='left',fill='x',expand=True);entry.bind('<Return>',lambda _:self.search())
        self.search_button=ttk.Button(bar,text='搜索地点',command=self.search);self.search_button.pack(side='left',padx=(8,0))
        ttk.Label(body,text='输入城市、街道或地标，例如 New York。搜索词发送给 Photon；地图由 OpenStreetMap 提供。',style='Muted.TLabel',wraplength=780).pack(anchor='w',pady=(6,8))
        self.list=tk.Listbox(body,height=3,exportselection=False,background=ui.SURFACE,foreground=ui.INK,highlightthickness=1,selectbackground=ui.BLUE,selectforeground='white')
        self.list.pack(fill='x',pady=(0,8));self.list.bind('<<ListboxSelect>>',lambda _:self.choose_result())
        controls=ttk.Frame(body);controls.pack(fill='x',pady=(0,6))
        for label,command in [('＋',lambda:self.change_zoom(1)),('−',lambda:self.change_zoom(-1)),('←',lambda:self.pan(-180,0)),('↑',lambda:self.pan(0,-180)),('↓',lambda:self.pan(0,180)),('→',lambda:self.pan(180,0))]:ttk.Button(controls,text=label,width=3,command=command).pack(side='left',padx=(0,4))
        ttk.Label(controls,text='点击选点 · 拖动平移',style='Muted.TLabel').pack(side='right')
        self.canvas=tk.Canvas(body,takefocus=True,background='#E6EBEE',highlightthickness=1,highlightbackground='#CFD7DC');self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Return>',lambda _:self.select_center())
        for key,delta in [('Left',(-180,0)),('Right',(180,0)),('Up',(0,-180)),('Down',(0,180))]:self.canvas.bind('<'+key+'>',lambda _,d=delta:self.pan(*d))
        self.canvas.bind('<Configure>',lambda _:self.schedule_draw());self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.motion);self.canvas.bind('<ButtonRelease-1>',self.release)
        attribution=ttk.Label(body,text='© OpenStreetMap contributors · 地图数据 ODbL',foreground=ui.BLUE,cursor='pointinghand');attribution.pack(anchor='e',pady=(4,0));attribution.bind('<Button-1>',lambda _:webbrowser.open('https://www.openstreetmap.org/copyright'))
        self.position=tk.StringVar();ttk.Label(body,textvariable=self.position).pack(anchor='w',pady=(8,2))
        self.status=tk.StringVar(value='选点后点击“使用此位置”，仅填回坐标，不会立即修改手机。');ttk.Label(body,textvariable=self.status,style='Muted.TLabel',wraplength=780).pack(fill='x')
        footer=ttk.Frame(body);footer.pack(fill='x',pady=(12,0))
        ttk.Button(footer,text='取消',command=self.close).pack(side='right');ttk.Button(footer,text='使用此位置',style='Primary.TButton',command=self.accept).pack(side='right',padx=8)
        self.update_position();self.window.after(100,self.poll)
    def update_position(self):self.position.set(f'已选：纬度 {self.selected[0]:.6f}，经度 {self.selected[1]:.6f}')
    def search(self):
        if self.searching:return
        query=self.query.get().strip()
        if not query:self.status.set('先输入地点名称，再点击搜索。');return
        self.searching=True;self.search_button.configure(state='disabled');self.status.set('正在搜索地点…')
        def run():
            try:self.events.put(('results',search_places(query)))
            except Exception as exc:self.events.put(('search_error',str(exc)))
        threading.Thread(target=run,daemon=True).start()
    def choose_result(self):
        indices=self.list.curselection()
        if not indices or indices[0]>=len(self.results):return
        result=self.results[indices[0]];self.lat=result['latitude'];self.lon=result['longitude'];self.selected=(self.lat,self.lon);self.zoom=14;self.failed.clear();self.generation+=1
        self.update_position();self.status.set('已选搜索结果，可在地图上点击微调。');self.schedule_draw()
    def change_zoom(self,delta):self.zoom=max(2,min(18,self.zoom+delta));self.generation+=1;self.failed.clear();self.schedule_draw()
    def pan(self,dx,dy):
        x,y=project(self.lat,self.lon,self.zoom);self.lat,self.lon=unproject(x+dx,y+dy,self.zoom);self.generation+=1;self.schedule_draw()
    def select_center(self):
        self.selected=(self.lat,self.lon);self.update_position();self.schedule_draw()
    def press(self,event):self.drag_start=(event.x,event.y,self.lat,self.lon)
    def motion(self,event):
        if not self.drag_start:return
        x,y,lat,lon=self.drag_start;cx,cy=project(lat,lon,self.zoom);self.lat,self.lon=unproject(cx-(event.x-x),cy-(event.y-y),self.zoom);self.schedule_draw()
    def release(self,event):
        if not self.drag_start:return
        x,y,_,_=self.drag_start;self.drag_start=None
        if abs(event.x-x)+abs(event.y-y)<6:
            cx,cy=project(self.lat,self.lon,self.zoom);self.selected=unproject(cx+event.x-self.canvas.winfo_width()/2,cy+event.y-self.canvas.winfo_height()/2,self.zoom);self.update_position();self.status.set('已选地图位置。点击“使用此位置”填回定位窗口。')
        self.generation+=1;self.schedule_draw()
    def schedule_draw(self):
        if not self.closed and self.draw_token is None:self.draw_token=self.window.after(100,self.draw)
    def draw(self):
        self.draw_token=None
        if self.closed:return
        c=self.canvas;c.delete('all');width,height=c.winfo_width(),c.winfo_height();cx,cy=project(self.lat,self.lon,self.zoom);left,top=cx-width/2,cy-height/2;n=2**self.zoom;needed=set()
        for x in range(math.floor(left/256),math.floor((left+width)/256)+1):
            for y in range(max(0,math.floor(top/256)),min(n-1,math.floor((top+height)/256))+1):
                key=(self.zoom,x%n,y);needed.add(key);px,py=x*256-left,y*256-top
                if key in self.images:c.create_image(px,py,image=self.images[key],anchor='nw')
                else:
                    c.create_rectangle(px,py,px+256,py+256,outline='#CFD7DC')
                    if key not in self.pending and key not in self.failed and not self.drag_start:
                        self.pending.add(key);generation=self.generation
                        def load(k=key,g=generation):
                            if self.closed or g!=self.generation:self.events.put(('tile_cancel',k));return
                            try:self.events.put(('tile',k,tile_data(*k)))
                            except Exception as exc:self.events.put(('tile_error',k,str(exc)))
                        _TILE_POOL.submit(load)
        # Bound memory to the current viewport, keeping pending downloads out of Tk.
        self.images={k:v for k,v in self.images.items() if k in needed}
        sx,sy=project(*self.selected,self.zoom);sx=(sx-cx+n*128)%(n*256)-n*128+width/2;sy=sy-top
        c.create_oval(sx-8,sy-8,sx+8,sy+8,fill=ui.BLUE,outline='white',width=3);c.create_line(sx-12,sy,sx+12,sy,fill=ui.BLUE);c.create_line(sx,sy-12,sx,sy+12,fill=ui.BLUE)
        if self.failed:c.create_text(width/2,22,text='部分地图未加载；可重试缩放或使用搜索结果坐标。',fill=ui.INK)
    def poll(self):
        if self.closed:return
        redraw=False
        try:
            while True:
                event=self.events.get_nowait();kind=event[0]
                if kind in ('results','search_error'):
                    self.searching=False;self.search_button.configure(state='normal')
                    if kind=='results':
                        self.results=event[1];self.list.delete(0,'end')
                        for result in self.results:self.list.insert('end',result['label'])
                        self.status.set('请选择一个搜索结果。' if self.results else '未找到地点，可尝试英文名称或更完整的地址。')
                    else:self.status.set('地点搜索失败，请检查网络后重试：'+event[1][:160])
                else:
                    self.pending.discard(event[1])
                    if kind=='tile':
                        try:
                            image=Image.open(io.BytesIO(event[2]));image.load()
                            if image.size!=(256,256):raise ValueError('地图图片尺寸无效')
                            self.images[event[1]]=ImageTk.PhotoImage(image,master=self.window)
                        except Exception:self.failed.add(event[1])
                    elif kind=='tile_error':self.failed.add(event[1])
                    redraw=True
        except queue.Empty:pass
        if redraw:self.schedule_draw()
        self.window.after(100,self.poll)
    def accept(self):self.on_select(*self.selected);self.close()
    def close(self):
        self.closed=True;self.window.destroy()
        if self.on_close:self.on_close()
