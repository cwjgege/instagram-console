# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with the isolated location venv, never the GUI venv."""
import argparse,subprocess,sys
from pathlib import Path
from importlib.metadata import version
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).parent.name=='tools' else Path(__file__).resolve().parent
SOURCE=ROOT/'location_helper/worker.py' if (ROOT/'location_helper/worker.py').exists() else ROOT/'worker.py'
def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'build/location');args=p.parse_args()
 if version('pymobiledevice3')!='11.44.0':raise SystemExit('Install location_helper/requirements.lock first.')
 out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
 subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--console','--onedir','--name','ig-location-helper',
  '--distpath',str(out/'dist'),'--workpath',str(out/'work'),'--specpath',str(out),
  '--copy-metadata','pymobiledevice3','--collect-data','certifi','--collect-data','pymobiledevice3',
  str(SOURCE)],check=True,cwd=ROOT)
 helper=out/'dist/ig-location-helper'
 subprocess.run([str(helper/'ig-location-helper'),'--self-test'],check=True)
 print(helper)
if __name__=='__main__':main()
