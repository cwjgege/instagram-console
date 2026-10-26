# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare dependency sources + notices using exact installed versions and PyPI hashes.
Run in the isolated location venv. All runtime/build Python dependencies are included,
not only packages inferred to require source distribution.
"""
import certifi,ssl,time
import argparse,concurrent.futures,hashlib,json,shutil,urllib.request,zipfile
from pathlib import Path
from importlib.metadata import distributions
ROOT=Path(__file__).resolve().parents[1]
def read_url(url):
 for attempt in range(4):
  try:
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
   with opener.open(url,timeout=60) as response:return response.read()
  except Exception:
   if attempt==3:raise
   time.sleep(attempt+1)

def fetch(dist,destination):
 name=dist.metadata['Name'];v=dist.version
 meta=json.loads(read_url(f'https://pypi.org/pypi/{name}/{v}/json'))
 sources=[x for x in meta['urls'] if x['packagetype']=='sdist']
 if not sources:raise RuntimeError(f'{name} {v}: missing source distribution; resolve before publishing')
 item=sources[0];target=destination/item['filename'];expected=item['digests']['sha256']
 if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected:
  target.write_bytes(read_url(item['url']))
 if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise RuntimeError('Source hash mismatch: '+name)
 return {'name':name,'version':v,'filename':item['filename'],'sha256':expected,'source_url':item['url']}
def main():
 p=argparse.ArgumentParser();p.add_argument('output',type=Path);args=p.parse_args();out=args.output.resolve();sources=out/'dependencies';sources.mkdir(parents=True,exist_ok=True)
 ds=list(distributions());notices=ROOT/'licenses/location';notices.mkdir(parents=True,exist_ok=True)
 for dist in ds:
  folder=notices/(dist.metadata['Name']+'-'+dist.version);folder.mkdir(exist_ok=True)
  (folder/'METADATA.txt').write_text(dist.read_text('METADATA') or dist.read_text('PKG-INFO') or '')
  for file in dist.files or []:
   if '.dist-info/' in str(file) and any(x in str(file).lower() for x in ('license','copying','notice','author')):
    source=Path(dist.locate_file(file))
    if source.is_file():shutil.copy2(source,folder/str(file).split('.dist-info/',1)[1].replace('/','_'))
 with concurrent.futures.ThreadPoolExecutor(8) as pool:manifest=list(pool.map(lambda d:fetch(d,sources),ds))
 (out/'manifest.json').write_text(json.dumps(sorted(manifest,key=lambda x:x['name'].lower()),indent=2)+'\n')
 for file in ('worker.py','requirements.txt','requirements.lock','README.md','COPYING'):
  shutil.copy2(ROOT/'location_helper'/file,out/file)
 for file in ('build_location.py','location_sources.py'):shutil.copy2(ROOT/'tools'/file,out/file)
 print('Source archives verified:',len(manifest))
if __name__=='__main__':main()
