"""Local task alerts. Delivery failure must never interrupt device control."""
import json,subprocess,sys,threading,time
from pathlib import Path

SCRIPT='''on run argv
 display notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv) sound name "Glass"
end run'''

def deliver(title,device,message):
    if sys.platform!='darwin':return False
    try:
        subprocess.run(['/usr/bin/osascript','-e',SCRIPT,title,device,message],
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=8,check=True)
        return True
    except (OSError,subprocess.SubprocessError):return False

class TaskAlerts:
    def __init__(self,runtime,device,send=deliver):
        self.path=Path(runtime)/'task-alerts.json';self.device=device;self.send=send;self.seen=set()
    def handle(self,generation,row):
        event=row.get('event')
        if event not in ('error','feature_disabled','resume_blocked','location_unconfirmed','location_recovered'):return False
        key=(generation,event,row.get('feature',''))
        if key in self.seen:return False
        self.seen.add(key)
        title={'error':'任务已中断，需要处理','feature_disabled':'部分功能已暂停','resume_blocked':'继续托管需要处理',
               'location_unconfirmed':'定位保持无法确认','location_recovered':'定位读回已恢复'}[event]
        # No captions, queries, account names, paths or raw HTTP diagnostics in banners.
        body={'error':'请打开 Instagram 控制台查看原因。当前动作不会重复发送。',
              'feature_disabled':'搜索或其他可选功能未通过检查，详情见设备运行记录。',
              'resume_blocked':'手机页面或连接尚未就绪，任务保持暂停。',
              'location_unconfirmed':'位置读回持续无效或设备校验失败，无法保证仍在所选位置。请打开固定定位窗口检查。',
              'location_recovered':'已收到新鲜位置样本，确认回到所选位置范围。'}[event]
        try:
            try:history=json.loads(self.path.read_text())
            except (OSError,ValueError):history=[]
            if not isinstance(history,list):history=[]
            history.append({'time':time.time(),'title':title,'message':body,'event':event})
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(history[-30:],ensure_ascii=False,indent=2));temp.replace(self.path)
        except OSError:pass
        threading.Thread(target=self.send,args=(title,self.device,body),daemon=True).start()
        return True
