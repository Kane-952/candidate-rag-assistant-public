from backend.app.models.schemas import Chunk
from backend.app.rag.context_selection import select_context, profile_intent, asks_transition_reason


def test_overview_recalls_multiple_documents_and_education():
    chunks = [Chunk(chunk_id=str(i),source=f'projects/{i}.md',title='项目概要',text=f'项目{i}') for i in range(4)]
    chunks += [Chunk(chunk_id='edu', source='resume/resume.md', title='教育背景', text='学校资料')]
    assert len(select_context('候选人做过什么项目？', [], chunks)) == 4
    assert select_context('候选人在哪个大学学习？', [], chunks)[0].chunk_id == 'edu'
    assert select_context('候选人获得过诺贝尔奖吗？', [], chunks) == []


def test_context_is_bounded_and_deduplicated():
    chunks = [Chunk(chunk_id=str(i),source=f'{i%3}.md',title='项目概要',text='资料') for i in range(60)]
    result = select_context('做过什么项目', [], chunks, limit=12)
    assert len(result) == 12
    assert len({c.source for c in result}) == 3
    assert len({c.chunk_id for c in result}) == 12


def test_broad_introduction_prioritizes_ai_and_explicit_work_keeps_work_evidence():
    from backend.app.models.schemas import SearchHit
    work = Chunk(chunk_id='work', source='resume/resume.md', title='简历 / 工作或实习经历 / 公司：示例公司', text='此前做过运营工作')
    school = Chunk(chunk_id='school', source='resume/resume.md', title='简历 / 教育背景', text='当前硕士')
    project = Chunk(chunk_id='rag', source='projects/rag.md', title='RAG 项目概要', text='已完成 RAG 项目')
    goal = Chunk(chunk_id='goal', source='projects/about.md', title='个人简介 / 当前目标', text='寻找 AI 实习')
    chunks = [work, school, project, goal]
    ranked = [SearchHit(**work.model_dump(), score=1)]
    for query in ('介绍一下你的背景', '简单介绍一下你自己', '你的经历是什么？', '说说你的个人情况'):
        assert profile_intent(query) == 'general'
        selected = select_context(query, ranked, chunks)
        assert 'work' not in [c.chunk_id for c in selected]
        assert {'school', 'rag', 'goal'} <= {c.chunk_id for c in selected}
    for query in ('你之前做过什么工作？', '研究生之前的工作经历是什么？', '为什么从之前的行业转到 AI？'):
        assert profile_intent(query) == 'work'
        assert 'work' in [c.chunk_id for c in select_context(query, [], chunks)]
    assert asks_transition_reason('为什么从之前的行业转到 AI？')
    assert profile_intent('介绍你的项目经历') == 'other'
    assert profile_intent('为实习岗位介绍一下你的背景') == 'general'


def test_internship_questions_recall_explicit_gap_and_projects_without_formal_jobs():
    from backend.app.models.schemas import SearchHit
    work = Chunk(chunk_id='work', source='resume/resume.md', title='简历 / 正式工作经历 / 公司：示例公司', text='正式运营工作')
    internship = Chunk(chunk_id='internship', source='resume/resume.md', title='简历 / 实习经历', text='目前尚无正式 AI 实习经历。')
    project = Chunk(chunk_id='project', source='projects/rag.md', title='RAG 项目概要', text='完成 RAG 实践')
    ranked = [SearchHit(**work.model_dump(), score=1)]
    chunks = [work, internship, project]
    for query in ('你有实习经历吗？', '之前做过什么实习？', '介绍一下你的实习经历', '有没有相关方向的实习经验？'):
        assert profile_intent(query) == 'internship'
        ids = {c.chunk_id for c in select_context(query, ranked, chunks)}
        assert 'internship' in ids and 'project' in ids and 'work' not in ids
    assert profile_intent('你之前做过什么工作？') == 'work'
    work_ids = {c.chunk_id for c in select_context('你之前做过什么工作？', [], chunks)}
    assert 'work' in work_ids and 'internship' not in work_ids
    mixed_ids = {c.chunk_id for c in select_context('介绍你的工作和实习经历', [], chunks)}
    assert {'work', 'internship'} <= mixed_ids


def test_strength_questions_recall_relevant_evidence_without_unsolicited_gaps():
    from backend.app.models.schemas import SearchHit
    internship = Chunk(chunk_id='internship', source='resume/resume.md', title='简历 / 实习经历', text='暂无 AI 实习')
    work = Chunk(chunk_id='work', source='resume/resume.md', title='简历 / 正式工作经历', text='运营工作')
    education = Chunk(chunk_id='education', source='resume/resume.md', title='简历 / 教育背景', text='硕士研究方向')
    project = Chunk(chunk_id='project', source='projects/rag.md', title='RAG 项目概要', text='RAG 实践')
    skill = Chunk(chunk_id='skill', source='resume/resume.md', title='简历 / 技能', text='Python 和 PyTorch')
    chunks = [internship, work, education, project, skill]
    ranked = [SearchHit(**internship.model_dump(), score=1)]
    for query in ('你有什么优势？', '你为什么适合这个岗位？', '你的竞争力是什么？',
                  '为什么应该考虑你？', '你为什么适合大模型实习岗位？'):
        assert profile_intent(query) == 'strength'
        ids = {c.chunk_id for c in select_context(query, ranked, chunks)}
        assert {'education', 'project', 'skill'} <= ids
        assert 'internship' not in ids and 'work' not in ids
    assert profile_intent('你有相关方向的实习经验吗？') == 'internship'
