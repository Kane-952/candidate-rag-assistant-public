import httpx
import pytest

from backend.app.config import Settings
from backend.app.errors import LLMUnavailable
from backend.app.llm.client import LLMClient


def make_client(handler):
    client = LLMClient(Settings(_env_file=None, llm_api_key='test-secret', llm_model='test'))
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_invalid_judgement_fails_closed():
    client = make_client(lambda request: httpx.Response(200, json={'choices':[{'message':{'content':'{"answerable":"true"}'}}]}))
    try:
        assert not client.judge_answerability('ACM?', []).answerable
    finally:
        client.close()


def test_api_error_does_not_leak_response_or_key():
    client = make_client(lambda request: httpx.Response(401, text='test-secret'))
    try:
        with pytest.raises(LLMUnavailable) as exc:
            client.generate('prompt', {})
        assert 'test-secret' not in str(exc.value)
    finally:
        client.close()


def test_timeout_has_actionable_error():
    def timeout(request):
        raise httpx.ReadTimeout('test-secret', request=request)
    client = make_client(timeout)
    try:
        with pytest.raises(LLMUnavailable, match='超时'):
            client.generate('prompt', {})
    finally:
        client.close()

def test_thinking_mode_is_opt_in_and_truncation_is_explicit():
    import json
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{
            'finish_reason': 'length',
            'message': {'content': '', 'reasoning_content': 'test-secret'},
        }]})

    client = make_client(respond)
    try:
        with pytest.raises(LLMUnavailable, match='截断') as exc:
            client.generate('prompt', {})
        assert 'thinking' not in bodies[0]
        assert 'test-secret' not in str(exc.value)
        client.settings.llm_thinking_mode = 'disabled'
        with pytest.raises(LLMUnavailable, match='截断'):
            client.generate('prompt', {})
        assert bodies[1]['thinking'] == {'type': 'disabled'}
    finally:
        client.close()



def test_grounded_answer_requests_first_person_without_changing_evidence_rules():
    from unittest.mock import Mock
    client = make_client(lambda request: httpx.Response(200, json={'choices': [{'message': {'content': '{"answer":"我在项目中使用了 LoRA。[aaaaaaaaaaaaaaaaaaaa]","evidence_ids":["aaaaaaaaaaaaaaaaaaaa"],"refused":false}'}}]}))
    try:
        captured = Mock(wraps=client.generate)
        client.generate = captured
        from backend.app.models.schemas import SearchHit
        evidence = [SearchHit(chunk_id='a' * 20, source='resume/resume.md', title='项目', text='候选人在项目中使用了 LoRA。', score=1)]
        result = client.grounded_answer('你在项目中用了什么？', evidence)
        assert result.answer.startswith('我在')
        prompt = captured.call_args.args[0]
        assert '第一人称' in prompt and '不得虚构' in prompt and '资料不足时仍须拒答' in prompt
    finally:
        client.close()


def test_profile_prompts_focus_on_current_work_without_hiding_explicit_history():
    from unittest.mock import Mock
    from backend.app.models.schemas import SearchHit
    evidence = [SearchHit(chunk_id='a' * 20, source='resume/resume.md', title='教育背景', text='目前在读硕士。', score=1)]
    client = make_client(lambda request: httpx.Response(500))
    try:
        client.generate = Mock(return_value='{"answerable":true,"reason":"有证据","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}')
        client.judge_answerability('介绍一下你的背景', evidence)
        assert '不要求覆盖 Context 中全部经历' in client.generate.call_args.args[0]
        client.generate.return_value = '{"answer":"我目前在读硕士。[aaaaaaaaaaaaaaaaaaaa]","evidence_ids":["aaaaaaaaaaaaaaaaaaaa"],"refused":false}'
        client.grounded_answer('简单介绍一下你自己', evidence)
        prompt = client.generate.call_args.args[0]
        assert '不要主动展开早期电商运营' in prompt
        assert '正在学习、计划开展' in prompt
        client.grounded_answer('你之前做过什么工作？', evidence)
        assert '如实回答相关公司、岗位、时间与主要职责' in client.generate.call_args.args[0]
        client.grounded_answer('为什么从之前的行业转到 AI？', evidence)
        assert '不能把时间先后' in client.generate.call_args.args[0]
        client.generate.return_value = '{"answerable":true,"reason":"诚实说明","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}'
        client.verify_answer('为什么从之前的行业转到 AI？', '具体动机尚无法确认。', evidence)
        assert '资料未写明动机时' in client.generate.call_args.args[0]
    finally:
        client.close()


def test_internship_prompts_separate_formal_work_from_project_preparation():
    from unittest.mock import Mock
    from backend.app.models.schemas import SearchHit
    evidence = [SearchHit(chunk_id='a' * 20, source='resume/resume.md', title='实习经历',
                          text='目前还没有正式的 AI / 大模型相关实习经历。', score=1)]
    client = make_client(lambda request: httpx.Response(500))
    try:
        client.generate = Mock(return_value='{"answerable":true,"reason":"有明确记录","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}')
        client.judge_answerability('你有实习经历吗？', evidence)
        assert '正式工作不能当作实习' in client.generate.call_args.args[0]
        client.generate.return_value = '{"answer":"我目前还没有正式的 AI / 大模型相关实习经历。[aaaaaaaaaaaaaaaaaaaa]","evidence_ids":["aaaaaaaaaaaaaaaaaaaa"],"refused":false}'
        client.grounded_answer('介绍一下你的实习经历', evidence)
        prompt = client.generate.call_args.args[0]
        assert '不要说没有任何工作经验' in prompt
        assert '不要把学习或个人项目说成正式实习' in prompt
        client.generate.return_value = '{"answerable":true,"reason":"有明确记录","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}'
        client.verify_answer('有没有相关方向的实习经验？', '我目前还没有正式的 AI / 大模型相关实习经历。', evidence)
        assert '不得把运营工作写成实习' in client.generate.call_args.args[0]
        client.rewrite_query([], '你有实习经历吗？')
        assert '实习经历与正式工作经历不能互相改写' in client.generate.call_args.args[0]
    finally:
        client.close()


def test_strength_prompts_answer_relevant_facts_without_unsolicited_weaknesses():
    from unittest.mock import Mock
    from backend.app.models.schemas import SearchHit
    evidence = [SearchHit(chunk_id='a' * 20, source='projects/rag.md', title='RAG 项目概要',
                          text='完成 RAG 检索与可信问答实践。', score=1)]
    client = make_client(lambda request: httpx.Response(500))
    try:
        client.generate = Mock(return_value='{"answerable":true,"reason":"有证据","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}')
        client.judge_answerability('你有什么优势？', evidence)
        assert '不要求补充未被问到的实习状态' in client.generate.call_args.args[0]
        client.generate.return_value = '{"answer":"我完成了 RAG 检索与可信问答实践。[aaaaaaaaaaaaaaaaaaaa]","evidence_ids":["aaaaaaaaaaaaaaaaaaaa"],"refused":false}'
        client.grounded_answer('为什么应该考虑你？', evidence)
        prompt = client.generate.call_args.args[0]
        assert '不要主动提“没有相关实习经历”' in prompt
        assert '不得把个人项目说成正式岗位经验' in prompt
        client.generate.return_value = '{"answerable":true,"reason":"有证据","confidence":0.95,"evidence_ids":["aaaaaaaaaaaaaaaaaaaa"]}'
        client.verify_answer('你为什么适合这个岗位？', '我完成了 RAG 实践。', evidence)
        assert '未被问到时' in client.generate.call_args.args[0]
    finally:
        client.close()
