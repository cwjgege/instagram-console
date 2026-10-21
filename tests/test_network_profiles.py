import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import connections as cn
import device_manager as dm
import network_profiles as np
from fleet import DeviceStore

class PortableConnections(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.data=patch.object(dm,'DATA',self.root);self.data.start()
  self.locks=patch('app_paths.lock_device',return_value=Mock(__enter__=Mock(),__exit__=Mock(return_value=False)));self.locks.start()
  self.store=DeviceStore(self.root,template=json.loads(Path('src/default-config.json').read_text()))
  self.item={'udid':'a'*40,'name':'My Phone','fingerprint':'phone-one','wda_url':'http://192.168.1.4:18100','preview_url':'http://192.168.1.4:18101'}
  self.info={'fingerprint':'phone-one','name':'iPhone','ios':'18.1','bundle':'com.test.xctrunner','url':self.item['wda_url']}
 def tearDown(self):self.locks.stop();self.data.stop();self.tmp.cleanup()
 def package(self,items=None):return {'format':np.FORMAT,'version':1,'devices':items or [self.item]}
 def read(self,value):
  path=self.root/'import.json';path.write_text(json.dumps(value));return np.read_package(path)
 def test_import_verifies_original_fingerprint_and_never_starts_installs(self):
  with patch.object(cn,'checked',return_value=self.info) as check,patch.object(cn,'ensure_wifi') as startup:
   result=np.enroll(self.store,self.read(self.package())[0]);startup.assert_not_called();check.assert_called_once_with(self.item['wda_url'],'phone-one')
  self.assertEqual(result['ios'],'18.1');self.assertEqual(cn.load(self.item['udid'])['mode'],'remote')
 def test_bad_fingerprint_and_offline_keep_old_configuration(self):
  self.store.add({'udid':self.item['udid'],'name':'Original'})
  cn.save(self.item['udid'],{'mode':'remote',**{k:self.item[k] for k in ('wda_url','fingerprint')}})
  originals={p:p.read_bytes() for p in self.root.rglob('*.json')}
  for error in (RuntimeError('wrong fingerprint'),OSError('offline')):
   with patch.object(cn,'checked',side_effect=error),self.assertRaises(type(error)):np.enroll(self.store,self.item)
   self.assertEqual({p:p.read_bytes() for p in originals},originals)
 def test_same_phone_different_id_reuses_profile_and_keeps_browsing_settings(self):
  self.store.add({'udid':self.item['udid'],'name':'Original'})
  cfg=self.store.profiles()[0];cfg['enable_likes']=True;cfg['ai_api_key']='secret';self.store.save(cfg)
  cn.save(self.item['udid'],{'mode':'wifi','fingerprint':'phone-one'})
  with patch.object(cn,'checked',return_value=self.info):result=np.enroll(self.store,{**self.item,'udid':'b'*40,'identifier_kind':'wda'})
  self.assertEqual(result['udid'],self.item['udid']);self.assertEqual(cn.load(self.item['udid'])['mode'],'wifi');self.assertEqual(cn.load(self.item['udid'])['identifier_kind'],'udid');self.assertTrue(self.store.profiles()[0]['enable_likes']);self.assertEqual(len(self.store.profiles()),1)
 def test_export_whitelists_connection_fields_and_converts_wifi_to_existing_service(self):
  self.store.add({'udid':self.item['udid'],'name':'Named'})
  cn.save(self.item['udid'],{'mode':'wifi','fingerprint':'phone-one','certificate':'SECRET'})
  dm.write_json(dm.device_dir(self.item['udid'])/'network-runtime.json',{**self.info,'preview_url':self.item['preview_url'],'secret':'SECRET'})
  package=np.export_package(self.store.profiles());raw=json.dumps(package)
  self.assertNotIn('SECRET',raw);self.assertNotIn('certificate',raw);self.assertNotIn('mode',package['devices'][0]);self.assertEqual(self.read(package)[0]['wda_url'],self.item['wda_url'])
 def test_running_device_does_not_change_configuration(self):
  with patch.object(cn,'checked',return_value=self.info),self.assertRaisesRegex(ValueError,'正在运行'):np.enroll(self.store,self.item,lambda _:True)
  self.assertFalse((dm.device_dir(self.item['udid'])/'connection.json').exists())
 def test_partial_write_rolls_back_existing_files(self):
  self.store.add({'udid':self.item['udid'],'name':'Old'})
  originals={p:p.read_bytes() for p in self.root.rglob('*.json')}
  with patch.object(cn,'checked',return_value=self.info),patch.object(self.store,'add',side_effect=OSError('disk full')),self.assertRaises(OSError):np.enroll(self.store,self.item)
  self.assertEqual({p:p.read_bytes() for p in self.root.rglob('*.json')},originals)
 def test_discovery_without_udid_generates_stable_explicit_wda_identifier(self):
  value={k:v for k,v in self.item.items() if k!='udid'}
  row=np.normalize_device(value);self.assertEqual(row,np.normalize_device(value));self.assertEqual(row['identifier_kind'],'wda');self.assertEqual(len(row['udid']),32)
 def test_invalid_packages_and_paths_are_rejected(self):
  for value in ({}, {'format':np.FORMAT,'version':2,'devices':[self.item]},self.package([self.item,self.item]),self.package([{**self.item,'udid':'../../x'}]),self.package([{**self.item,'wda_url':'http://8.8.8.8:8100'}]),self.package([{**self.item,'fingerprint':''}])):
   with self.assertRaises(ValueError):self.read(value)
 def test_import_relocates_changed_ip_only_by_original_fingerprint(self):
  new='http://192.168.1.5:18100'
  def check(url,pin):
   if url!=new:raise OSError('old IP offline')
   return {**self.info,'url':new}
  with patch.object(cn,'checked',side_effect=check),patch.object(np,'discover_services',return_value={'devices':[{**self.item,'wda_url':new}]}):
   result=np.import_devices(self.store,[self.item])
  self.assertEqual(len(result['saved']),1);self.assertFalse(result['failed']);self.assertEqual(cn.load(self.item['udid'])['wda_url'],new)
 def test_import_partial_failure_leaves_healthy_peer_available(self):
  other={**self.item,'udid':'b'*40,'fingerprint':'second','name':'Offline'}
  def check(url,pin):
   if pin=='second':raise OSError('offline')
   return self.info
  with patch.object(cn,'checked',side_effect=check),patch.object(np,'discover_services',return_value={'devices':[]}):result=np.import_devices(self.store,[self.item,other])
  self.assertEqual(len(result['saved']),1);self.assertEqual(len(result['failed']),1);self.assertEqual(len(self.store.profiles()),1)
 def test_oversize_file_is_not_loaded(self):
  path=self.root/'huge.json';path.write_bytes(b' '* (np.MAX_FILE+1))
  with self.assertRaisesRegex(ValueError,'过大'):np.read_package(path)

class LanDiscovery(unittest.TestCase):
 def test_lan_scope_excludes_vpn_inactive_and_caps_large_subnet(self):
  listing='''en0: flags=x
 inet 192.168.31.119 netmask 0xffffff00
 status: active
utun3: flags=x
 inet 100.64.1.2 netmask 0xff000000
 status: active
en1: flags=x
 inet 10.0.0.2 netmask 0xffff0000
 status: inactive
en2: flags=x
 inet 172.20.5.7 netmask 0xffff0000
 status: active
'''
  with patch.object(np.subprocess,'run',return_value=Mock(returncode=0,stdout=listing)):
   hosts,nets=np.local_targets()
  self.assertEqual(nets,['172.20.5.0/24','192.168.31.0/24']);self.assertLessEqual(len(hosts),508);self.assertNotIn('192.168.31.119',hosts);self.assertFalse(any(x.startswith('100.') or x.startswith('10.') for x in hosts))
 def test_discovery_deduplicates_aliases_and_does_not_write(self):
  info={'fingerprint':'pin','ios':'18.1','name':'iPhone','bundle':'com.test','url':'http://192.168.1.4:18100'}
  with patch.object(np,'local_targets',return_value=(['192.168.1.4'],['192.168.1.0/24'])),patch.object(np,'known_endpoints',return_value={}),patch.object(np.socket,'create_connection',return_value=Mock(__enter__=Mock(),__exit__=Mock())),patch.object(cn,'inspect',return_value=info),patch.object(np,'preview_address',return_value=''),patch.object(cn,'save') as save:
   result=np.discover_services();self.assertEqual(len(result['devices']),1);save.assert_not_called()
 def test_endpoint_reused_by_other_phone_does_not_inherit_udid(self):
  info={'fingerprint':'new','ios':'18.1','name':'iPhone','bundle':'com.test','url':'http://192.168.1.4:18100'}
  with patch.object(np,'local_targets',return_value=([],[])),patch.object(np,'known_endpoints',return_value={info['url']:{'fingerprint':'old','udid':'a'*40}}),patch.object(np.socket,'create_connection',return_value=Mock(__enter__=Mock(),__exit__=Mock())),patch.object(cn,'inspect',return_value=info),patch.object(np,'preview_address',return_value=''):
   self.assertNotIn('udid',np.discover_services()['devices'][0])
