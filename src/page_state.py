"""Conservative, local UI-state recognition. Never sends a device action."""
from dataclasses import dataclass

class PageBlocked(RuntimeError):
    """Human action is required; optional features must not swallow this."""

def visible_nodes(tree):
    return [e for e in tree.iter() if e.get('visible')=='true']

def screen_height(tree):
    heights=[float(e.get('height',0)) for e in tree.iter() if e.tag in ('XCUIElementTypeApplication','XCUIElementTypeWindow')]
    return max(heights,default=896) or 896

def search_input_node(tree):
    nodes=visible_nodes(tree)
    if any(e.tag in ('XCUIElementTypeAlert','XCUIElementTypeSecureTextField') or e.get('name')=='ig-partial-modal-sheet-view-controller-content' for e in nodes):return None
    allowed={('XCUIElementTypeSearchField','search-text-input'),('XCUIElementTypeTextView','search-bar-text-view')}
    fields=[e for e in nodes if (e.tag,e.get('name')) in allowed and e.get('enabled','true')=='true'
        and float(e.get('y',0))+float(e.get('height',44))<=screen_height(tree)*.25]
    # A bottom follow-up composer is not the keyword search input.
    return fields[0] if len(fields)==1 else None

def search_layout(tree,term):
    nodes=visible_nodes(tree);names={e.get('name') for e in nodes}
    if any(e.tag in ('XCUIElementTypeKeyboard','XCUIElementTypeAlert','XCUIElementTypeSecureTextField') or e.get('name')=='ig-partial-modal-sheet-view-controller-content' for e in nodes):return None
    if any(e.tag in ('XCUIElementTypeTextField','XCUIElementTypeTextView') and e.get('name')!='search-bar-text-view' for e in nodes):return None
    if any(e.tag=='XCUIElementTypeTextView' and e.get('value','').strip() for e in nodes):return None
    legacy=[e for e in nodes if e.tag=='XCUIElementTypeSearchField' and e.get('name')=='search-text-input' and e.get('value','').casefold()==term.casefold()]
    if len(legacy)==1 and any(e.get('name')=='search-for-you-serp' and e.get('value')=='1' for e in nodes):return 'classic'
    title=[e for e in nodes if e.tag=='XCUIElementTypeButton' and (e.get('name')==term or e.get('label')==term)
        and float(e.get('y',0))+float(e.get('height',0))<screen_height(tree)*.2]
    # Scrolling can hide the tab behind the fixed title; its selected state is still readable.
    selected=any(e.tag=='XCUIElementTypeButton' and e.get('label','').casefold()=='for you' and e.get('value')=='1' for e in tree.iter())
    competing=any(e.tag=='XCUIElementTypeButton' and e.get('label','').casefold() in ('profiles','audio','tags') and e.get('value')=='1' for e in nodes)
    videos=any(e.tag=='XCUIElementTypeCell' and e.get('name')=='media-discovery-cell' and e.get('label','').startswith('Video by ') for e in nodes)
    if len(title)==1 and selected and not competing and videos and {'search-bar-new-journey-button','search-bar-text-view'}<=names:return 'meta-search'
    return None

def search_back_name(tree):
    nodes=visible_nodes(tree)
    if not any(e.get('name')=='search-bar-with-cancel' for e in nodes):return None
    buttons=[e.get('name') for e in nodes if e.tag=='XCUIElementTypeButton' and e.get('name') in ('search-bar-back-button','Back')]
    return buttons[0] if len(buttons)==1 else None


def profile_back_name(tree):
    nodes=visible_nodes(tree)
    if not any(e.tag=='XCUIElementTypeCollectionView' and e.get('name')=='profile' for e in nodes):return None
    candidates=[e for e in nodes if e.tag=='XCUIElementTypeButton' and e.get('name')=='profile-back-button'
        and e.get('enabled','true')=='true' and float(e.get('y',0))<screen_height(tree)*.15]
    return 'profile-back-button' if len(candidates)==1 else None

@dataclass(frozen=True)
class PageState:
    kind: str
    reason: str
    search_input: bool = False
    video_controls: bool = False
    blocked: bool = False

def classify(tree, own_queries=(), expected_query=None):
    nodes=visible_nodes(tree);names={e.get('name','') for e in nodes}
    security_text=[]
    def collect(element,in_caption=False):
        in_caption=in_caption or 'caption' in element.get('name','').casefold()
        if not in_caption and element.get('visible')=='true' and element.tag=='XCUIElementTypeStaticText':
            security_text.append(element.get('label','').strip().casefold())
        for child in element:collect(child,in_caption)
    collect(tree)
    if any(e.tag=='XCUIElementTypeAlert' for e in nodes):
        return PageState('alert','出现系统或应用弹窗，请手动处理',blocked=True)
    if any(any(text==word or text.startswith(word+' ') or text.startswith(word+'.') for text in security_text) for word in ('confirm your identity','verify your identity','suspicious activity',
        'try again later','we restrict certain activity','account has been suspended','security check',
        '确认你的身份','验证你的身份','操作过于频繁','稍后重试')):
        return PageState('account_check','出现验证、操作限制或账号提示，请手动处理',blocked=True)
    # Restrict login text matching to actionable controls; video captions can mention login.
    login=any(e.tag=='XCUIElementTypeSecureTextField' for e in nodes) or any(
        e.tag=='XCUIElementTypeButton' and (e.get('label','').casefold() in ('log in','sign in','登录')
        or e.get('name','') in ('login-button','login_button')) for e in nodes)
    if login:return PageState('login','出现登录页面，请手动处理',blocked=True)
    fields=[e for e in nodes if e.tag in ('XCUIElementTypeTextView','XCUIElementTypeTextField','XCUIElementTypeSearchField')]
    known_search=bool(fields) and all(
        (e.tag,e.get('name')) in {('XCUIElementTypeTextView','search-bar-text-view'),('XCUIElementTypeSearchField','search-text-input')}
        and e.get('value','').strip() in ('','Search','Search with Meta AI',*own_queries) for e in fields)
    if fields and not known_search:
        return PageState('input_unknown','页面包含输入框，保留当前输入内容')
    if 'ig-partial-modal-sheet-view-controller-content' in names:
        return PageState('sheet','出现浮层，需确认关闭入口')
    if any(e.tag=='XCUIElementTypeKeyboard' for e in nodes) and not known_search:
        return PageState('input_unknown','页面含未识别的键盘，保留输入内容')
    queries=[expected_query] if expected_query else own_queries
    layout=next((search_layout(tree,q) for q in queries if q and search_layout(tree,q)),None)
    if layout:return PageState('search_results','已识别搜索结果：'+layout)
    field=search_input_node(tree)
    if field is not None and known_search:
        # Explore retains a top SearchField after dismissing focused search; it has no Back button.
        explore=any(e.get('name')=='explore-tab' and e.get('value')=='1' for e in nodes)
        if field.tag=='XCUIElementTypeSearchField' and explore and not any(e.tag=='XCUIElementTypeKeyboard' for e in nodes) and not search_back_name(tree):
            return PageState('explore','已识别发现页面及顶部搜索入口',search_input=True)
        return PageState('search_input','已识别顶部搜索输入区',search_input=True)
    if known_search:return PageState('search_context','已识别搜索上下文，尚未确认结果')
    is_reels=any(e.get('name')=='reels-tab' and e.get('value')=='1' for e in nodes) or 'Reels' in names or {'reels-viewer','sundial-viewer-video-collection-view'}<=names
    if profile_back_name(tree):return PageState('profile','已识别个人主页及返回入口')
    if is_reels and {'like-button','comment-button'}<=names:
        return PageState('reels','已识别短视频页面',video_controls=True)
    if any(e.get('name')=='explore-tab' and e.get('value')=='1' for e in nodes):
        return PageState('explore','已识别发现页面')
    if 'reels-tab' in names:return PageState('main','已识别主导航，尚未确认视频')
    return PageState('unknown','页面未识别，等待确认可靠入口')
