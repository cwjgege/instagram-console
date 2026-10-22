import json,sys,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from runner import Runner,NavigationLost,InteractionSkipped,ControlHTTPError,TransportLost
from page_state import PageBlocked
CFG=json.loads(Path('src/default-config.json').read_text())

class InteractionContinuation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='work');self.root=Path(self.tmp.name).resolve()
 def tearDown(self):self.tmp.cleanup()
 def scenario(self,kind,failure):
  r=Runner({**CFG,'runtime_dir':str(self.root),'device_udid':'a'*20,'max_swipes_per_run':2,
   'enable_likes':kind=='likes','enable_saves':kind=='saves','save_probability':1,'enable_comments':False,
   'enable_search':False,'relevance_terms':['topic'],'exclude_keywords':[],'like_interval_min':1,'like_interval_max':1},notify=lambda _:None)
  r.like_next=1;r.connect=Mock();r.reels=Mock();r.diagnose=Mock();r.wait=Mock()
  r.rng=Mock();r.rng.uniform.side_effect=lambda a,b:a;r.rng.random.return_value=.5;r.rng.choices.return_value=[1];r.rng.randint.return_value=1
  state={'post':0,'clicked':False,'failed':False};clicks=[]
  name='like-button' if kind=='likes' else 'save-button';inactive='Like' if kind=='likes' else 'Save';active='Unlike' if kind=='likes' else 'Unsave'
  def read():
   if state['post']==0 and state['clicked'] and not state['failed'] and failure=='navigation':
    state['failed']=True;raise NavigationLost('temporary save sheet')
   if state['post']==0 and state['clicked'] and failure=='read_transport' and not state['failed']:raise TransportLost('read connection lost')
   label=active if state['clicked'] and (state['post']>0 or failure!='unconfirmed') else inactive
   return {'key':'post'+str(state['post']),'author':'demo','caption':'topic','nodes':[ET.Element('Button',name=name,label=label)]}
  def click(control):
   clicks.append((state['post'],control));state['clicked']=True
   if state['post']==0 and failure=='transport':raise TimeoutError('timed out after sending click')
   if state['post']==0 and failure=='blocked':raise PageBlocked('account check')
  def swipe(direction):r.swipes+=1;state.update(post=state['post']+1,clicked=False)
  r.read_reel=read;r.click=click;r.swipe=swipe
  def reconnect():
   self.assertFalse(r.in_interaction);state['failed']=True
  r.recover_connection=Mock(side_effect=reconnect)
  with patch.dict('os.environ',{'IG_CONSOLE_LOCK_DIR':str(self.root/'locks')}):status=r.run()
  return r,status,clicks
 def test_unconfirmed_like_and_save_continue_without_repeating_click(self):
  for kind in ('likes','saves'):
   with self.subTest(kind=kind):
    r,status,clicks=self.scenario(kind,'unconfirmed')
    self.assertEqual(status,'complete');self.assertEqual([p for p,_ in clicks],[0,1]);self.assertEqual(r.swipes,2)
    self.assertEqual(r.daily['posts']['post0'][kind],'pending');self.assertEqual(r.daily['posts']['post1'][kind],'done')
    self.assertFalse(r.in_interaction);self.assertEqual(r.counts[kind],1)
    # Keep each subtest's ledger separate.
    r.ledger_path.unlink()
 def test_transient_sheet_after_click_does_not_end_browsing(self):
  r,status,clicks=self.scenario('saves','navigation')
  self.assertEqual(status,'complete');self.assertEqual([p for p,_ in clicks],[0,1]);self.assertEqual(r.daily['posts']['post0']['saves'],'pending')
 def test_click_timeout_never_replays_write_and_next_post_can_continue(self):
  r,status,clicks=self.scenario('likes','transport')
  self.assertEqual(status,'complete');self.assertEqual([p for p,_ in clicks],[0,1]);self.assertEqual(r.counts['likes'],1)
 def test_connection_recovery_after_uncertain_like_never_replays_it(self):
  r,status,clicks=self.scenario('likes','read_transport')
  self.assertEqual(status,'complete');r.recover_connection.assert_called_once()
  self.assertEqual([p for p,_ in clicks],[0,1]);self.assertEqual(r.daily['posts']['post0']['likes'],'pending')
 def test_account_block_still_stops(self):
  r,status,clicks=self.scenario('likes','blocked')
  self.assertEqual(status,'error');self.assertEqual(r.swipes,0);self.assertEqual(len(clicks),1)
 def test_changed_post_before_click_is_skipped_without_reserving(self):
  r=Runner({**CFG,'runtime_dir':str(self.root)},notify=lambda _:None);r.read_reel=Mock(return_value={'key':'new'});r.click=Mock()
  with self.assertRaises(InteractionSkipped):r.toggle_on({'key':'old'},'likes','like-button',{'like'})
  r.click.assert_not_called();self.assertEqual(r.daily['likes'],0)
 def test_stale_control_is_not_reclicked(self):
  r=Runner({**CFG,'runtime_dir':str(self.root)},notify=lambda _:None);p={'key':'post','nodes':[ET.Element('Button',name='save-button',label='Save')]}
  r.read_reel=Mock(return_value=p);r.click=Mock(side_effect=ControlHTTPError('stale',404,'stale element reference'))
  with self.assertRaises(InteractionSkipped):r.toggle_on(p,'saves','save-button',{'save'})
  r.toggle_on(p,'saves','save-button',{'save'});r.click.assert_called_once()
