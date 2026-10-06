"""Bounded section recall for profile questions; all passages still require grounding."""
import re
from ..models.schemas import SearchHit


def profile_intent(query):
    """Keep internships, formal work, strengths and broad introductions separate."""
    internship_question = re.search(r'实习(经历|经验|过|情况|岗位经验|$)|做过.{0,8}实习|之前.{0,8}实习|有没有.{0,8}实习|有.{0,8}实习(吗|过)', query)
    if internship_question and not re.search(r'工作|任职|运营|电商|转行|转型', query):
        return 'internship'
    if re.search(r'工作经历|实习经历|职业经历|做过.{0,8}(工作|实习)|以前.{0,8}(工作|实习)|之前.{0,8}(工作|实习|行业)|实习过|工作过|任职|运营|电商|转行|转型|转到\s*AI|转向\s*AI', query, re.I):
        return 'work'
    if re.search(r'优势|竞争力|胜任|岗位匹配|为什么.{0,12}适合|为何.{0,12}适合|为什么.{0,12}考虑你|为何.{0,12}考虑你', query):
        return 'strength'
    if re.search(r'项目经历|研究经历|科研经历|技术经历|哪些项目|什么项目|项目介绍', query):
        return 'other'
    if re.search(r'自我介绍|个人情况|个人背景|背景|简介|履历|介绍.{0,8}(自己|个人|经历)|经历是什么|什么经历|说说.{0,8}经历', query):
        return 'general'
    return 'other'


def asks_transition_reason(query):
    return bool(
        re.search(r'转行|转型|转到|转向|进入', query)
        and re.search(r'AI|人工智能|大模型', query, re.I)
        and re.search(r'为什么|为何|原因|动机|怎么|如何', query)
    )


def is_work_section(chunk):
    return bool(re.search(r'工作或实习经历|工作经历|实习经历|公司：', chunk.title))


def is_formal_work_section(chunk):
    return bool(re.search(r'正式工作经历|工作或实习经历|(?<!实习)工作经历', chunk.title))


def is_internship_section(chunk):
    return bool(re.search(r'(^| / )实习经历(?: / |$)', chunk.title))


def select_context(query, ranked, chunks, limit=32):
    topics = []
    intent = profile_intent(query)
    if intent == 'general':
        # A broad introduction should not pull every earlier job into the answer.
        ranked = [c for c in ranked if not is_work_section(c)]
        topics += ['个人简介', '基本信息', '教育', '当前目标', '职业兴趣', '技术特点', '项目概要', '技能']
    elif intent == 'strength':
        ranked = [c for c in ranked if not is_work_section(c)]
        topics += ['教育', '技能', '技术特点', '项目概要', '项目简介', '实现方式', '当前目标', '职业兴趣']
    elif intent == 'internship':
        ranked = [c for c in ranked if not is_formal_work_section(c)]
        topics += ['实习经历', '技能', '技术特点', '项目概要', '当前目标']
    elif intent == 'work':
        if '实习' not in query:
            ranked = [c for c in ranked if not is_internship_section(c)]
        topics += ['正式工作经历', '工作或实习经历', '工作经历', '个人简介', '当前目标', '职业兴趣']
        if '实习' in query:
            topics.append('实习经历')
    elif re.search(r'介绍|概况', query):
        topics += ['个人简介', '项目概要']
    if re.search(r'大学|学校|学历|教育|毕业', query):
        topics += ['教育', '个人简介']
    if re.search(r'联系|邮箱|电话|微信', query):
        topics += ['联系方式', '基本信息']
    if re.search(r'哪些技术|技术栈|掌握|技能', query):
        topics += ['技能', '技术', '实现方式']
    if re.search(r'什么项目|哪些项目|项目经历|经历和项目', query):
        topics += ['项目概要', '项目简介', '项目背景', '项目目标']
    extra = [c for c in chunks if any(t in c.title for t in topics)
             and (intent != 'general' or not is_work_section(c))
             and (intent != 'strength' or not is_work_section(c))
             and (intent != 'internship' or not is_formal_work_section(c))
             and (intent != 'work' or '实习' in query or not is_internship_section(c))]
    # Round-robin across documents prevents a long project from filling the context.
    groups = {}
    for c in extra:
        groups.setdefault(c.source, []).append(c)
    selected = []
    while any(groups.values()) and len(selected) < limit - len(ranked):
        for group in groups.values():
            if group and len(selected) < limit - len(ranked):
                selected.append(group.pop(0))
    result, seen = [], set()
    for c in [*ranked, *selected]:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            result.append(c if isinstance(c, SearchHit) else SearchHit(**c.model_dump(), score=0))
    return result[:limit]
