import json,math,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path('src').resolve()))
import location_map as maps
class Projection(unittest.TestCase):
 def test_roundtrip_and_negative_longitude(self):
  for point in [(40.7128,-74.006),(0,0),(-33.8688,151.2093),(51.5,-.1)]:
   for zoom in (2,12,18):
    result=maps.unproject(*maps.project(*point,zoom),zoom)
    self.assertAlmostEqual(result[0],point[0],places=6);self.assertAlmostEqual(result[1],point[1],places=6)
 def test_latitude_poles_are_clamped_to_mercator_extent(self):
  for lat in (-90,90):
   x,y=maps.project(lat,0,12);self.assertTrue(math.isfinite(y));self.assertAlmostEqual(abs(maps.unproject(x,y,12)[0]),85.05112878,places=6)
 def test_world_wrap(self):
  self.assertAlmostEqual(maps.unproject(256*4+10,512,2)[1],maps.unproject(10,512,2)[1])
class Service(unittest.TestCase):
 def test_photon_result_parsing_and_invalid_coordinates_filtered(self):
  data={'features':[{'geometry':{'coordinates':[-74.006,40.7128]},'properties':{'name':'New York','city':'New York','country':'United States'}},{'geometry':{'coordinates':[200,100]},'properties':{}},{}]}
  with patch.object(maps,'cached',return_value=json.dumps(data).encode()),patch.object(maps,'_LAST_SEARCH',0):result=maps.search_places('New York')
  self.assertEqual(result,[{'label':'New York · United States','latitude':40.7128,'longitude':-74.006}])
 def test_same_name_city_and_state_are_distinguished(self):
  data={'features':[{'geometry':{'coordinates':[-74,40]},'properties':{'name':'New York','type':kind}} for kind in ('city','state')]}
  with patch.object(maps,'cached',return_value=json.dumps(data).encode()),patch.object(maps,'_LAST_SEARCH',0):result=maps.search_places('New York')
  self.assertIn('城市',result[0]['label']);self.assertIn('州／省',result[1]['label'])
 def test_empty_or_oversize_query_does_not_send_request(self):
  with patch.object(maps,'download') as download:
   for query in ('',' '*4,'x'*161):
    with self.assertRaises(ValueError):maps.search_places(query)
   download.assert_not_called()
 def test_visible_tile_wraps_world_and_cache_ttl_is_seven_days(self):
  with patch.object(maps,'cached',return_value=b'png') as cached:
   self.assertEqual(maps.tile_data(3,-1,2),b'png')
   self.assertIn('/3/7/2.png',cached.call_args.args[0]);self.assertEqual(cached.call_args.args[2],7*86400)
   self.assertIsNone(maps.tile_data(3,0,-1));self.assertEqual(cached.call_count,1)
 def test_repeat_search_uses_cache_without_second_network_request(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(maps,'DATA',Path(temp)),patch.object(maps,'download',return_value=b'{"features":[]}') as download:
   url='https://example.test/?q=New+York'
   self.assertEqual(maps.cached(url,'search',86400,100),maps.cached(url,'search',86400,100));download.assert_called_once()
 def test_configured_provider_requires_https(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(maps,'DATA',Path(temp)):
   (Path(temp)/'map-provider.json').write_text('{"search":"http://example.test"}')
   with self.assertRaises(ValueError):maps.provider()
 def test_old_cache_refreshed_and_size_limited(self):
  with tempfile.TemporaryDirectory() as temp,patch.object(maps,'DATA',Path(temp)),patch.object(maps,'download',return_value=b'new') as download:
   maps.cached('https://example.test','tiles',86400,100)
   file=next(Path(temp).rglob('*.cache'))
   import os
   os.utime(file,(time.time()-8*86400,)*2)
   self.assertEqual(maps.cached('https://example.test','tiles',86400,100),b'new');self.assertEqual(download.call_count,2)
