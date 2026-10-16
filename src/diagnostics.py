"""Bounded, allowlisted diagnostic bundles; config/credentials/interaction ledger excluded."""
import base64
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from app_paths import APP_VERSION

NAV_NAMES={'profile-back-button','profile','profile-more-bar-button','reels-tab','explore-tab','like-button','comment-button','save-button','back-button',
    'BackButton','Back','Cancel','search-text-input','search-bar-text-view','search-bar-with-cancel',
    'search-bar-back-button','search-bar-new-journey-button','search-for-you-serp','media-discovery-cell',
    'reels-viewer','sundial-viewer-video-collection-view','ig-partial-modal-sheet-view-controller-content'}
COUNTERS={'likes','saves','comments','watched','swipes','searches','search_videos','search_attempts','search_failures','recoveries','reconnections'}

def redact(value):
    text=str(value)[:1000]
    for pattern in (r'https?://\S+',r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',r'\b[0-9A-Fa-f]{8}-[0-9A-Fa-f-]{12,}\b',
        r'(?i)\b(?:sk-|bearer\s+)[\w.-]+',r'/Users/[^\s/]+(?:/[^\s]*)?'):
        text=re.sub(pattern,'[redacted]',text)
    return text

def structure(tree):
    if tree is None:return None
    root=ET.Element('page-structure')
    # Flat visible controls are enough to diagnose positions/types without post text or account names.
    for e in tree.iter():
        if e.get('visible')!='true':continue
        attrs={k:e.get(k) for k in ('visible','enabled','x','y','width','height') if e.get(k) is not None}
        if e.get('name') in NAV_NAMES:attrs['name']=e.get('name')
        if e.tag=='XCUIElementTypeButton' and e.get('value') in ('0','1'):attrs['selected']=e.get('value')
        ET.SubElement(root,e.tag,attrs)
    return ET.tostring(root,encoding='unicode')

def record(log_dir,stage,exc,tree,state,features,counts,screenshot=None):
    directory=Path(log_dir)/'diagnostics';directory.mkdir(exist_ok=True)
    cases=list(directory.glob('case-*.json'))
    if len(cases)>=5:return None
    stem='case-'+str(len(cases)+1).zfill(2)
    data={'app_version':APP_VERSION,'time':datetime.now().isoformat(timespec='seconds'),
        'stage':stage,'error_type':type(exc).__name__,'page_state':state.kind if state else 'unknown',
        'feature_enabled':{k:bool(v['enabled']) for k,v in features.items()},
        'counts':{k:v for k,v in counts.items() if k in COUNTERS},
        'has_screenshot':False,'privacy':'Structured records omit account names, post text, input values, device IDs, config, API keys and ledger. Optional screenshots may contain visible account/content information.'}
    xml=structure(tree)
    if xml is not None:(directory/(stem+'.xml')).write_text(xml,encoding='utf-8')
    if screenshot:
        try:
            image=base64.b64decode(screenshot,validate=True)
            suffix='.png' if image.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if image.startswith(b'\xff\xd8\xff') else None
            if suffix and len(image)<=15*1024*1024:
                (directory/(stem+suffix)).write_bytes(image);data['has_screenshot']=True
        except (ValueError,TypeError):pass
    (directory/(stem+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return directory

def export(runtime,destination,include_screenshots=False):
    """Export latest run only, to a user-selected ZIP. Never copy arbitrary runtime files."""
    runtime=Path(runtime).resolve()
    runs=runtime/'runs'
    available=sorted((p for p in runs.iterdir() if p.is_dir() and not p.is_symlink()),key=lambda p:p.name) if runs.exists() else []
    if not available:raise ValueError('尚无运行记录，请先运行一次')
    run=available[-1];directory=run/'diagnostics'
    files=[]
    if directory.is_dir() and not directory.is_symlink():
        for n in range(1,6):
            for suffix in ('.json','.xml')+(('.png','.jpg') if include_screenshots else ()):
                p=directory/('case-'+str(n).zfill(2)+suffix)
                if p.is_file() and not p.is_symlink():files.append(p)
    # Rebuild summary from a schema rather than exporting raw event logs (which contain captions/queries).
    summary={}
    p=run/'summary.json'
    if p.exists() and not p.is_symlink():
        raw=json.loads(p.read_text())
        summary={k:v for k,v in raw.items() if k in COUNTERS or k in ('status','page_state')}
        summary['counts']={k:v for k,v in raw.get('counts',{}).items() if k in COUNTERS}
        summary['feature_enabled']={k:bool(v.get('enabled')) for k,v in raw.get('feature_state',{}).items() if k=='search'}
    manifest={'app_version':APP_VERSION,'exported_at':datetime.now().isoformat(timespec='seconds'),
        'screenshots_included':bool(include_screenshots and any(p.suffix in ('.png','.jpg') for p in files)),
        'summary':summary,'cases':sum(p.suffix=='.json' for p in files),
        'excluded':['config','API keys','device identifiers','raw logs','interaction ledger','post text','input values']}
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        bundle.writestr('README.txt','本包仅包含最近一轮脱敏诊断。截图仅在明确选择时包含，截图可能含账号和视频内容。\n无异常的运行可能没有诊断案例。\n')
        for p in files:bundle.write(p,'diagnostics/'+p.name)
    return manifest
