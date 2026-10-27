"""Conservative local caption/hashtag matching for viewing time only."""
import math,re,unicodedata
STOP={'a','an','the','and','or','to','of','for','with','in','on','how'}
def normalized(value):
    value=unicodedata.normalize('NFKC',value)
    value=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1 \2',value)
    value=re.sub(r'([a-z0-9])([A-Z])',r'\1 \2',value)
    return ' '.join(re.findall(r'[^\W_]+',value.casefold()))
def phrase_matches(text,term):
    if not term:return False
    if any('\u3400'<=c<='\u9fff' for c in term):return term in text
    return ' '+term+' ' in ' '+text+' '
def watch_related(post,keywords,terms,excluded):
    if not post.get('key') or not post.get('caption','').strip():return False
    text=normalized(post['caption']);words=set(text.split())
    if any(phrase_matches(text,normalized(term)) for term in excluded if term.strip()):return False
    for query in (*keywords,*terms):
        query=normalized(query)
        if not query:continue
        if phrase_matches(text,query):return True
        tokens=list(dict.fromkeys(t for t in query.split() if t not in STOP))
        # Preserve the leading topic word when allowing a longer phrase variation.
        if len(tokens)>=2 and tokens[0] in words and len(set(tokens)&words)>=max(2,math.ceil(len(tokens)*2/3)):return True
    return False
