import sys,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path('src').resolve()))
from watch_relevance import watch_related
from watch_policy import watch_plan
from runner import Runner
CFG=json.loads(Path('src/default-config.json').read_text())
def post(caption):return {'key':'clip','caption':caption}
class WatchRelevance(unittest.TestCase):
 def test_search_keyword_is_used_when_content_terms_do_not_match(self):self.assertTrue(watch_related(post('AI video creation tutorial'),['AI video creation'],['gardening'],[]))
 def test_existing_content_terms_still_work(self):self.assertTrue(watch_related(post('A filmmaking tutorial'),[],['filmmaking'],[]))
 def test_keywords_allow_word_order_or_a_minor_phrase_variation(self):
  self.assertTrue(watch_related(post('AI tools for video editing'),['AI video creation'],[],[]))
  self.assertTrue(watch_related(post('Travel tips from New York'),['new york travel'],[],[]))
 def test_generic_video_word_without_topic_is_not_enough(self):self.assertFalse(watch_related(post('A video creation tutorial for travel'),['AI video creation'],[],[]))
 def test_ai_does_not_match_inside_hair_or_chair(self):self.assertFalse(watch_related(post('Hair styling on a chair'),['AI'],[],[]))
 def test_hashtags_punctuation_and_case_are_normalized(self):self.assertTrue(watch_related(post('#AIVideoCreation tutorial'),['AI video creation'],[],[]))
 def test_exclusion_takes_priority(self):self.assertFalse(watch_related(post('AI video creation about gambling'),['AI video creation'],[],['gambling']))
 def test_chinese_topic_supported_and_unknown_video_not_assumed_related(self):
  self.assertTrue(watch_related(post('人工智能视频制作分享'),['人工智能'],[],[]))
  self.assertFalse(watch_related({'key':None,'caption':'AI video'},['AI video'],[],[]))
 def test_unrelated_known_long_duration_still_uses_short_random_wait(self):
  rng=Mock();rng.uniform.return_value=4;rng.random.return_value=1
  plan=watch_plan({'playback':{'elapsed':0,'duration':60},'progress':1},False,rng)
  self.assertEqual(plan['basis'],'short');self.assertEqual(plan['seconds'],4);rng.uniform.assert_called_once_with(1.5,5)
 def test_unrelated_progress_never_starts_completion_polling(self):
  with tempfile.TemporaryDirectory() as directory:
   r=Runner({**CFG,'runtime_dir':directory},notify=lambda _:None);r.rng=Mock();r.rng.uniform.return_value=3;r.rng.random.return_value=1;r.wait=Mock();r.read_reel=Mock()
   with patch('runner.time.monotonic',return_value=100):r.watch_video({'key':'clip','progress':50},False)
   r.wait.assert_called_once_with(3);r.read_reel.assert_not_called()
 def test_viewing_match_does_not_expand_social_action_eligibility(self):
  with tempfile.TemporaryDirectory() as directory:
   r=Runner({**CFG,'runtime_dir':directory,'keywords':['AI video creation'],'relevance_terms':['gardening'],'exclude_keywords':[]},notify=lambda _:None)
   self.assertTrue(r.watch_related(post('AI video creation')));self.assertFalse(r.related(post('AI video creation')))

 def test_fast_swipe_burst_stops_at_matching_content(self):
  import os
  with tempfile.TemporaryDirectory() as directory:
   r=Runner({**CFG,'runtime_dir':directory,'device_udid':'a'*20,'keywords':['AI video creation'],'relevance_terms':['gardening'],'exclude_keywords':[],
    'enable_search':False,'enable_likes':False,'enable_saves':False,'enable_comments':False,'max_swipes_per_run':4},notify=lambda _:None)
   r.connect=Mock();r.reels=Mock();r.wait=Mock();r.watch_video=Mock();r.rng=Mock();r.rng.uniform.side_effect=lambda a,b:a;r.rng.random.return_value=1;r.rng.choices.return_value=[3]
   state={'i':0}
   def current():return {'key':str(state['i']),'caption':'AI video creation' if state['i']==1 else 'unrelated','author':'demo','nodes':[]}
   r.read_reel=current
   def swipe(direction):
    state['i']+=1;r.swipes+=1;r.last_swipe_post=current()
   r.swipe=swipe
   r.seconds=100
   with patch('runner.time.monotonic',side_effect=lambda:100 if r.swipes>=4 else 0),patch.dict(os.environ,IG_CONSOLE_LOCK_DIR=str(Path(directory)/'locks')):self.assertEqual(r.run(),'complete')
   watched=[(call.args[0]['key'],call.args[1]) for call in r.watch_video.call_args_list]
   self.assertIn(('1',True),watched)
