import base64,io,json,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from PIL import Image
import preview,device_manager as dm

class PreviewProtocol(unittest.TestCase):
 def jpeg(self):
  out=io.BytesIO();Image.new('RGB',(21,13),'blue').save(out,format='JPEG');return out.getvalue()
 def test_fragmented_stream_preserves_complete_frames(self):
  jpeg=self.jpeg();raw=b'HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace\r\n\r\n--frame\r\n'+jpeg+b'\r\n--frame\r\n'+jpeg
  parser=preview.FrameParser();frames=[]
  for i in range(0,len(raw),7):frames.extend(parser.feed(raw[i:i+7]))
  self.assertEqual(frames,[jpeg,jpeg])
 def test_truncated_frame_and_garbage_cannot_grow_memory_forever(self):
  parser=preview.FrameParser();parser.feed(b'x'*100000);self.assertLessEqual(len(parser.buffer),1)
  parser.feed(b'\xff\xd8'+b'x'*(preview.MAX_FRAME+1));self.assertEqual(len(parser.buffer),0)
  self.assertEqual(parser.feed(self.jpeg()),[self.jpeg()])
 def test_unconfigured_preview_never_initializes_control_service(self):
  with tempfile.TemporaryDirectory() as root,patch.object(dm,'DATA',Path(root)),patch.object(dm,'prepare_vendor') as vendor:
   with self.assertRaisesRegex(RuntimeError,'等待控制组件'):preview.stream_request('a'*40)
   vendor.assert_not_called()
 def test_invalid_port_or_device_cannot_start_helper(self):
  with tempfile.TemporaryDirectory() as root,patch.object(dm,'DATA',Path(root)),patch.object(dm,'prepare_vendor') as vendor:
   (Path(root)/'ports.json').write_text(json.dumps({'a'*40:{'mjpeg':True}}))
   with self.assertRaisesRegex(RuntimeError,'端口'):preview.stream_request('a'*40)
   with self.assertRaises(ValueError):preview.stream_request('../other')
   vendor.assert_not_called()

class PreviewIsolation(unittest.TestCase):
 def setUp(self):self.source=preview.PreviewSource(resolver=Mock(side_effect=RuntimeError('预览测试断线')))
 def tearDown(self):self.source.close();self.source.thread.join(2);self.assertFalse(self.source.thread.is_alive())
 def test_late_frame_from_previous_device_is_discarded(self):
  first=self.source.select('a'*40);second=self.source.select('b'*40)
  self.assertFalse(self.source.publish(first,image=Image.new('RGB',(1,1))))
  self.assertTrue(self.source.publish(second,image=Image.new('RGB',(2,2))))
  rev,frame,_=self.source.snapshot();self.assertEqual(rev,second);self.assertEqual(frame[1].size,(2,2))
 def test_pause_discards_inflight_frame_without_touching_browser(self):
  with patch('runner.call') as browser:
   rev=self.source.select('a'*40);self.source.select('a'*40,True)
   self.assertFalse(self.source.publish(rev,image=Image.new('RGB',(1,1))))
   browser.assert_not_called();self.assertIsNone(self.source.snapshot()[1])
 def test_stream_failure_is_local_status_only(self):
  with patch('runner.call') as browser,patch('runtime_manager.stop_all') as stop:
   self.source.select('a'*40)
   deadline=time.monotonic()+1
   while time.monotonic()<deadline and '测试断线' not in self.source.snapshot()[2]:time.sleep(.01)
   self.assertIn('测试断线',self.source.snapshot()[2]);browser.assert_not_called();stop.assert_not_called()
 def test_quality_changes_do_not_restart_or_touch_browser(self):
  with patch('runner.call') as browser:
   revision=self.source.revision
   for fps in (2,5,10):self.source.set_quality(fps,(280,600));self.assertEqual(self.source.fps,fps)
   self.assertEqual(self.source.revision,revision);browser.assert_not_called()
   with self.assertRaises(ValueError):self.source.set_quality(1000)
 def test_only_latest_frame_is_retained(self):
  rev=self.source.select('a'*40)
  for i in range(1,20):self.source.publish(rev,image=Image.new('RGB',(i,1)))
  self.assertEqual(self.source.snapshot()[1][1].width,19);self.assertIsNone(self.source.snapshot()[1])
 def test_close_terminates_only_the_preview_child(self):
  process=Mock();self.source.process=process
  with patch('runtime_manager.stop_all') as stop:self.source.close();process.terminate.assert_called_once();stop.assert_not_called()

class SnapshotPreview(unittest.TestCase):
 def source(self):
  source=preview.PreviewSource.__new__(preview.PreviewSource)
  source.lock=threading.Lock();source.target_size=(200,300);source.wake=threading.Event()
  source.current=Mock(side_effect=[True,False,False]);source.publish=Mock()
  return source
 def test_missing_mjpeg_can_read_pixels_without_creating_or_changing_a_session(self):
  import connections as cn
  out=io.BytesIO();Image.new('RGB',(400,600),'blue').save(out,format='PNG')
  source=self.source();setting={'mode':'remote','wda_url':'http://192.168.1.4:8100','fingerprint':'pin'}
  with patch.object(cn,'load',return_value=setting),patch.object(cn,'checked') as checked,patch.object(cn,'get',side_effect=[{'uuid':'pin'},base64.b64encode(out.getvalue()).decode()]) as get:
   source._snapshot_stream('a'*40,1)
   checked.assert_called_once_with(setting['wda_url'],'pin')
   self.assertEqual([c.args[1] for c in get.call_args_list],['/wda/device/info','/screenshot'])
   self.assertEqual(source.publish.call_args.kwargs['image'].size,(200,300))
 def test_dhcp_identity_change_never_reads_another_phones_pixels(self):
  import connections as cn
  source=self.source()
  with patch.object(cn,'load',return_value={'mode':'remote','wda_url':'http://192.168.1.4:8100','fingerprint':'pin'}),patch.object(cn,'checked'),patch.object(cn,'get',return_value={'uuid':'other'}) as get:
   with self.assertRaisesRegex(RuntimeError,'指纹'):source._snapshot_stream('a'*40,1)
   get.assert_called_once();source.publish.assert_not_called()

class PreviewDisplayIsolation(unittest.TestCase):
 def test_render_failure_keeps_preview_poll_alive(self):
  pane=preview.PreviewPane.__new__(preview.PreviewPane);pane.closed=False;pane.update_frame=Mock(side_effect=RuntimeError('display failed'));pane.state=Mock();pane.frame=Mock()
  pane.poll();pane.frame.after.assert_called_once_with(50,pane.poll);self.assertIn('异常',pane.state.set.call_args.args[0])
 def test_paused_frame_does_not_rescale_image_on_every_status_tick(self):
  pane=preview.PreviewPane.__new__(preview.PreviewPane);pane.source=Mock();pane.source.snapshot.return_value=(1,None,'预览已暂停')
  pane.paused=True;pane.image=Image.new('RGB',(2,4));pane.frame_time=time.time()-10;pane.updated=Mock();pane.zoom=None;pane.dirty=False;pane.canvas=Mock();pane.paint=Mock();pane.update_badge=Mock()
  pane.update_frame();pane.paint.assert_not_called();pane.update_badge.assert_called_once_with(pane.canvas)
