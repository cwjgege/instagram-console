"""Frozen macOS application entry point. No dependency on a system Python."""
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from app_paths import DATA,VERSION,APP_VERSION,load_config

def self_test(output):
    import tkinter
    import PIL.Image
    import ssl
    import device_manager as dm
    vendor=dm.prepare_vendor()
    env=dm.child_env(vendor)
    node=subprocess.check_output([str(vendor/'bin/node'),'--version'],env=env,text=True).strip()
    appium=subprocess.check_output([str(vendor/'bin/node'),str(vendor/'node_modules/appium/index.js'),'--version'],env=env,text=True).strip()
    assert (vendor/'legacy-wda/WebDriverAgent.xcodeproj').exists(), 'Missing iOS16 sources'
    assert (dm.ASSETS/'resources/ios_device.cjs').exists(), 'Missing USB bridge'
    result={'version':APP_VERSION,'python':sys.version.split()[0],'tk':tkinter.TkVersion,
            'pillow':PIL.Image.__version__,'tls_ca_certificates':ssl.create_default_context().cert_store_stats()['x509_ca'],'node':node,'appium':appium,
            'data_directory':str(DATA),'xcode':str(dm.xcode_directory()),'standalone':bool(getattr(sys,'frozen',False))}
    if getattr(sys,'frozen',False):
        from location_manager import worker_command
        result['location_backend']=json.loads(subprocess.check_output(worker_command()+['--self-test'],text=True,timeout=30))
    dm.write_json(Path(output),result)

def main():
    if getattr(sys,'frozen',False):
        import certifi
        os.environ.setdefault('SSL_CERT_FILE',certifi.where())
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        self_test(sys.argv[2]);return
    if len(sys.argv)==4 and sys.argv[1]=='--check-connection':
        import connections as cn
        try:
            setting=cn.load(sys.argv[2]);info=cn.checked(setting['wda_url'],setting['fingerprint']);result={'ready':True,'info':info}
        except Exception as exc:result={'ready':False,'error':type(exc).__name__+': '+str(exc)}
        Path(sys.argv[3]).write_text(json.dumps(result,ensure_ascii=False,indent=2));return
    DATA.mkdir(parents=True,exist_ok=True)
    log=(DATA/'console.log').open('a',buffering=1)
    sys.stdout=sys.stderr=log
    print(f'Instagram Console {APP_VERSION} · {sys.executable}',flush=True)
    lock=(DATA/'application.lock').open('a')
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk()
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        root.withdraw();messagebox.showinfo('Instagram 控制台 '+APP_VERSION,'控制台已有实例在运行。\n升级后请先完全退出正在运行的控制台，再打开 '+APP_VERSION+'。直接打开新安装包不会切换已运行的旧版。',parent=root)
        root.destroy();return
    fleet=None
    try:
        load_config()
        from fleet import Fleet
        fleet=Fleet(root);root.mainloop()
    except Exception:
        import traceback
        traceback.print_exc()
        messagebox.showerror('启动失败','请查看应用数据目录中的 console.log。\n'+str(DATA),parent=root)
    finally:
        import device_manager
        import runtime_manager
        if fleet is not None:fleet.locations.shutdown()
        runtime_manager.stop_all()
        device_manager.stop_owned_server()
        lock.close()

if __name__=='__main__':main()
