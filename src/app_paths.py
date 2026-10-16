"""Writable user data and relocatable application resources."""
import json
import os
import sys
import re
import fcntl
from pathlib import Path

FROZEN=bool(getattr(sys,'frozen',False))
ASSETS=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
SOURCE=Path(__file__).resolve().parent
PORT=14723 if FROZEN else 4723
SERVER=f'http://127.0.0.1:{PORT}'+('/igconsole' if FROZEN else '')
DATA=Path(os.environ.get('IG_CONSOLE_DATA_DIR',str(Path.home()/'Library/Application Support/Instagram Console'))) if FROZEN else SOURCE
VERSION='1.1.0'  # Bundled runtime cache version; unchanged for this UI/runner update.
APP_VERSION='1.1.8'

def lock_device(udid):
    if not re.fullmatch('[A-Fa-f0-9-]{10,50}',udid):raise RuntimeError('请先配置有效的 iPhone')
    directory=Path(os.environ.get('IG_CONSOLE_LOCK_DIR',str(Path.home()/'Library/Application Support/Instagram Console/locks')))
    directory.mkdir(parents=True,exist_ok=True)
    guard=(directory/(udid+'.lock')).open('a')
    try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        guard.close();raise RuntimeError('这台 iPhone 正被另一个控制任务使用，请先停止旧版或其他任务。')
    return guard

def load_config():
    DATA.mkdir(parents=True,exist_ok=True)
    path=DATA/'config.json'
    if not path.exists():
        defaults=json.loads((ASSETS/'default-config.json').read_text())
        defaults['device_udid']=''
        defaults['runtime_dir']=str(DATA/'devices/unconfigured')
        path.write_text(json.dumps(defaults,ensure_ascii=False,indent=2)+'\n')
    config=json.loads(path.read_text())
    Path(config['runtime_dir']).mkdir(parents=True,exist_ok=True)
    return config
