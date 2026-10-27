"""Bound ordinary feed dwell using observed player timing, with a short fallback."""
import math,re

CLOCK=r'(?:\d{1,2}:)?\d{1,2}:[0-5]\d(?:\.\d+)?'
PAIR=re.compile(r'('+CLOCK+r')\s*(?:/|of|out of|共)\s*('+CLOCK+r')',re.I)

def clock_seconds(value):
    parts=[float(p) for p in value.split(':')]
    return sum(part*60**index for index,part in enumerate(reversed(parts)))

def playback_timing(nodes):
    """Never infer duration from captions, counters, percentages or a generic label."""
    values=set()
    for node in nodes:
        if node.tag not in ('XCUIElementTypeSlider','XCUIElementTypeProgressIndicator') or node.get('visible')!='true':continue
        identity=(node.get('name','')+' '+node.get('label','')).casefold()
        if not any(word in identity for word in ('playback','video progress','seek','scrub','播放进度','视频进度')):continue
        for text in (node.get('value',''),node.get('label','')):
            match=PAIR.search(text)
            if not match:continue
            elapsed,total=map(clock_seconds,match.groups())
            if 0<=elapsed<=total and .25<=total<=3600:values.add((elapsed,total))
    if len(values)!=1:return None
    elapsed,total=values.pop();return {'elapsed':elapsed,'duration':total}

def playback_progress(nodes):
    values=set()
    for node in nodes:
        if node.get('visible')!='true' or node.tag not in ('XCUIElementTypeOther','XCUIElementTypeSlider','XCUIElementTypeProgressIndicator'):continue
        for text in (node.get('name',''),node.get('label','')):
            match=re.fullmatch(r'Video progress:\s*(\d{1,3}(?:\.\d+)?)\s*percent',text,re.I)
            if match and 0<=float(match[1])<=100:values.add(float(match[1]))
    return values.pop() if len(values)==1 else None

def playback_finished(previous,current):
    if current is None:return False
    return current>=97 or (previous is not None and previous>=65 and current<=35 and previous-current>=40)

def watch_plan(post,related,rng):
    if not related:return {'seconds':rng.uniform(2,5),'basis':'short','duration':None}
    timing=post.get('playback')
    if isinstance(timing,dict):
        elapsed,total=timing.get('elapsed'),timing.get('duration')
        if (type(elapsed) in (int,float) and type(total) in (int,float)
            and math.isfinite(elapsed) and math.isfinite(total) and 0<=elapsed<=total and .25<=total<=3600):
            # Aim near one viewing, allow a small variation, and sample long videos.
            return {'seconds':max(0,min(25,total*rng.uniform(.85,1.15))-elapsed),'basis':'duration','duration':total}
    progress=post.get('progress')
    if type(progress) in (int,float) and math.isfinite(progress) and 0<=progress<=100:
        return {'seconds':rng.uniform(18,25) if related else rng.uniform(2,5),'basis':'progress','duration':None}
    return {'seconds':rng.uniform(4,10) if related else rng.uniform(2,5),'basis':'fallback','duration':None}
