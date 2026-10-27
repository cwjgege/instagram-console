"""Saved device identities and explicit, recoverable local removal."""
import copy,ctypes,json,threading,time,uuid
from pathlib import Path
from app_paths import DATA,load_config
import device_manager as dm
import connections

IDENTITY_FIELDS=('device_name','device_original_name','device_alias','device_ios')
def normalized(udid):return udid.replace('-','').lower()
def display_name(cfg):return cfg.get('device_alias') or cfg.get('device_name') or 'iPhone'
def preserve_identity(cfg,path):
    """A settings window must not overwrite a newer scan/rename."""
    result=dict(cfg)
    try:current=json.loads(Path(path).read_text())
    except (OSError,ValueError):return result
    if current.get('device_udid')!=result.get('device_udid'):return result
    for key in IDENTITY_FIELDS:
        if key in current:result[key]=current[key]
    return result

def trash_directory(path):
    """Use macOS Trash semantics, including its restore metadata and volume handling."""
    path=Path(path).resolve()
    foundation=ctypes.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    objc=ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes=[ctypes.c_char_p];objc.objc_getClass.restype=ctypes.c_void_p
    objc.sel_registerName.argtypes=[ctypes.c_char_p];objc.sel_registerName.restype=ctypes.c_void_p
    address=ctypes.cast(objc.objc_msgSend,ctypes.c_void_p).value
    def send(target,selector,restype,args=(),types=()):
        fn=ctypes.CFUNCTYPE(restype,ctypes.c_void_p,ctypes.c_void_p,*types)(address)
        return fn(target,objc.sel_registerName(selector.encode()),*args)
    cls=lambda name:objc.objc_getClass(name.encode())
    ptr=ctypes.c_void_p
    pool=send(send(cls('NSAutoreleasePool'),'alloc',ptr),'init',ptr)
    try:
        text=send(cls('NSString'),'stringWithUTF8String:',ptr,(str(path).encode(),),(ctypes.c_char_p,))
        url=send(cls('NSURL'),'fileURLWithPath:',ptr,(text,),(ptr,))
        manager=send(cls('NSFileManager'),'defaultManager',ptr)
        error=ptr();destination=ptr()
        ok=send(manager,'trashItemAtURL:resultingItemURL:error:',ctypes.c_bool,
                (url,ctypes.byref(destination),ctypes.byref(error)),(ptr,ctypes.POINTER(ptr),ctypes.POINTER(ptr)))
        if not ok:
            message='无法移至系统废纸篓，设备资料未删除。'
            if error.value:
                desc=send(error.value,'localizedDescription',ptr)
                message=send(desc,'UTF8String',ctypes.c_char_p).decode('utf-8')
            raise OSError(message)
    finally:send(pool,'drain',None)

class DeviceStore:
    def __init__(self,root=None,template=None,trash=trash_directory):
        self.root=Path(root or DATA);self.template=copy.deepcopy(template if template is not None else load_config())
        self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.trash=trash
        old=self.template.get('device_udid')
        if old and not self.is_removed(old) and not self.path(old).exists():
            self.save(self.make({'udid':old,'name':self.template.get('device_name','iPhone'),'ios':self.template.get('device_ios','')}))
    def path(self,udid):
        import re
        if not isinstance(udid,str) or not re.fullmatch('[A-Fa-f0-9-]{10,50}',udid):raise ValueError('设备标识无效')
        return self.root/'devices'/udid/'config.json'
    def _registry(self):
        path=self.root/'device-registry.json'
        if not path.exists():return {'removed':{}}
        value=json.loads(path.read_text())
        if not isinstance(value,dict) or not isinstance(value.get('removed'),dict):raise ValueError('设备管理记录无法读取，请保留记录并检查文件。')
        return value
    def is_removed(self,udid):
        self.path(udid)
        with self.lock:return normalized(udid) in self._registry()['removed']
    def make(self,device):
        cfg=copy.deepcopy(self.template);udid=device['udid'];self.path(udid)
        for key in ('device_alias','device_original_name'):cfg.pop(key,None)
        cfg.update(device_udid=udid,device_name=device.get('name','iPhone'),device_original_name=device.get('name','iPhone'),device_ios=device.get('ios',''),runtime_dir=str(self.path(udid).parent),managed_device=True)
        cfg.pop('server_url',None);return cfg
    def save(self,cfg):
        with self.lock:
            udid=cfg['device_udid']
            if self.is_removed(udid):raise ValueError('此设备已删除，请通过“添加设备”重新添加。')
            value=preserve_identity(copy.deepcopy(cfg),self.path(udid));path=self.path(udid)
            value['runtime_dir']=str(path.parent);value['managed_device']=True;dm.write_json(path,value)
    def add(self,device,explicit=False):
        with self.lock:
            udid=device['udid'];path=self.path(udid);removed=self.is_removed(udid)
            if removed and not explicit:return None
            if removed:
                # Never resurrect files left behind by an interrupted removal.
                self._trash_records([udid])
            cfg=json.loads(path.read_text()) if path.exists() else self.make(device)
            cfg.setdefault('device_original_name',cfg.get('device_name','iPhone'))
            if device.get('name') and device['name']!='iPhone':cfg['device_original_name']=device['name']
            cfg['device_name']=cfg.get('device_alias') or cfg['device_original_name']
            if device.get('ios') and device['ios']!='未知':cfg['device_ios']=device['ios']
            cfg['runtime_dir']=str(path.parent);cfg['managed_device']=True
            dm.write_json(path,cfg)
            if removed:
                registry=self._registry();registry['removed'].pop(normalized(udid),None)
                try:dm.write_json(self.root/'device-registry.json',registry)
                except Exception:
                    # Written config remains hidden until the registry update succeeds.
                    raise
            return cfg
    def rename(self,udid,name):
        if not isinstance(name,str):raise ValueError('请输入设备名称。')
        name=name.strip()
        if not 1<=len(name)<=40 or any(ord(c)<32 for c in name):raise ValueError('设备名称须为 1～40 个字符，不含换行等控制字符。')
        with self.lock:
            if self.is_removed(udid):raise ValueError('此设备已删除。')
            path=self.path(udid);cfg=json.loads(path.read_text())
            cfg.setdefault('device_original_name',cfg.get('device_name','iPhone'))
            cfg.update(device_alias=name,device_name=name);dm.write_json(path,cfg);return cfg
    def _related_ids(self,udid):
        ids=[udid]
        for path in (self.root/'devices').glob('*/connection.json'):
            try:
                value=json.loads(path.read_text())
                if normalized(value.get('merged_into',''))==normalized(udid):self.path(path.parent.name);ids.append(path.parent.name)
            except (OSError,ValueError,TypeError,AttributeError):continue
        return list(dict.fromkeys(ids))
    def _trash_records(self,ids):
        sources=[self.path(i).parent for i in ids if self.path(i).parent.exists()]
        if not sources:return
        stage=self.root/('Deleted-iPhone-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        stage.mkdir();moved=[]
        try:
            for source in sources:
                target=stage/source.name;source.rename(target);moved.append((source,target))
            self.trash(stage)
        except Exception:
            for source,target in reversed(moved):
                if target.exists():target.rename(source)
            if stage.exists():stage.rmdir()
            raise
    def remove(self,udid,cleanup=lambda ids:None):
        with self.lock:
            if not self.path(udid).exists():raise ValueError('设备记录已不存在，请刷新列表。')
            ids=self._related_ids(udid);registry=self._registry();before=copy.deepcopy(registry)
            for i in ids:registry['removed'][normalized(i)]={'udid':i,'removed_at':time.time()}
            dm.write_json(self.root/'device-registry.json',registry)
            try:cleanup(ids);self._trash_records(ids)
            except Exception:
                dm.write_json(self.root/'device-registry.json',before);raise
            return ids
    def profiles(self):
        with self.lock:
            rows=[];removed=self._registry()['removed']
            for path in sorted((self.root/'devices').glob('*/config.json')):
                if normalized(path.parent.name) in removed:continue
                try:
                    cfg=json.loads(path.read_text());self.path(cfg['device_udid'])
                    if path.parent.name!=cfg['device_udid']:continue
                    if connections.merged_target(cfg['device_udid']):continue
                    cfg.setdefault('device_original_name',cfg.get('device_name','iPhone'))
                    cfg['device_name']=display_name(cfg);cfg['runtime_dir']=str(path.parent);cfg['managed_device']=True;rows.append(cfg)
                except (OSError,ValueError,KeyError,TypeError):
                    try:cfg=self.make({'udid':path.parent.name,'name':'配置文件异常'});cfg['_load_error']='配置文件无法读取，请检查该设备 config.json。';rows.append(cfg)
                    except ValueError:continue
            return rows
