"""Build on Apple Silicon with Python 3.14/Tk 9 and Node installed locally."""
import json,os,platform,plistlib,shutil,subprocess,sys,tarfile,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build';STAGE=BUILD/'stage';SOURCE=ROOT/'src';VENDOR=STAGE/'vendor'
def run(args,**kwargs):return subprocess.run([str(a) for a in args],check=True,**kwargs)
def main():
    if sys.platform!='darwin' or platform.machine()!='arm64':raise SystemExit('Build requires Apple Silicon macOS.')
    import tkinter
    if sys.version_info[:2]!=(3,14) or tkinter.TkVersion<9:raise SystemExit('Use Python 3.14 with Tcl/Tk 9.')
    node=shutil.which('node');npm=shutil.which('npm')
    if not node or not npm:raise SystemExit('Install Node.js and npm first.')
    run([npm,'ci'],cwd=ROOT)
    (VENDOR/'bin').mkdir(parents=True,exist_ok=True)
    shutil.copy2(node,VENDOR/'bin/node')
    if (VENDOR/'node_modules').exists():shutil.rmtree(VENDOR/'node_modules')
    shutil.copytree(ROOT/'node_modules',VENDOR/'node_modules',symlinks=True)
    shutil.copy2(ROOT/'tools/driver-template.json',VENDOR/'driver-template.json')
    legacy=BUILD/'legacy-package';legacy.mkdir(parents=True,exist_ok=True)
    packed=subprocess.check_output([npm,'pack','appium-webdriveragent@12.2.2','--pack-destination',str(legacy),'--json'],text=True,cwd=ROOT)
    package=legacy/json.loads(packed)[0]['filename']
    with tarfile.open(package) as bundle:bundle.extractall(legacy,filter='data')
    if (VENDOR/'legacy-wda').exists():shutil.rmtree(VENDOR/'legacy-wda')
    shutil.copytree(legacy/'package',VENDOR/'legacy-wda')
    archive=STAGE/'vendor.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=6) as bundle:
        for child in VENDOR.iterdir():bundle.add(child,arcname=child.name)
    shutil.copy2(SOURCE/'default-config.json',STAGE/'default-config.json')
    license_source=ROOT/'licenses'
    location=Path(os.environ.get('IG_LOCATION_BACKEND',str(BUILD/'location/dist/ig-location-helper')))
    if not (location/'ig-location-helper').is_file():raise SystemExit('Build tools/build_location.py in its isolated venv first, or set IG_LOCATION_BACKEND.')
    run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
         '--name','Instagram 控制台','--target-arch','arm64','--osx-bundle-identifier','local.igconsole.desktop',
         '--distpath',ROOT/'dist','--workpath',BUILD/'pyinstaller','--specpath',BUILD,
         '--paths',SOURCE,'--collect-all','PIL','--collect-all','certifi',
         '--add-data',f'{STAGE / "default-config.json"}:.','--add-data',f'{archive}:.',
         '--add-data',f'{location}:location-backend','--add-data',f'{license_source}:licenses','--add-data',f'{SOURCE / "resources"}:resources',SOURCE/'app_entry.py'],cwd=ROOT)
    app=ROOT/'dist/Instagram 控制台.app';info=app/'Contents/Info.plist'
    version=re.search(r"^APP_VERSION='([^']+)'",(SOURCE/'app_paths.py').read_text(),re.M)[1]
    values=plistlib.loads(info.read_bytes());values.update(CFBundleShortVersionString=version,CFBundleVersion=version,NSHighResolutionCapable=True,
        NSLocalNetworkUsageDescription='连接你选择并验证的局域网 iPhone，读取设备状态和手机预览，并运行你启动的控制任务。')
    info.write_bytes(plistlib.dumps(values));run(['/usr/bin/codesign','--force','--deep','--sign','-',app])
    print(app)
if __name__=='__main__':main()
