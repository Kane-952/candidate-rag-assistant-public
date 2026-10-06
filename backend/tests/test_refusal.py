from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.config import Settings
from backend.app.models.schemas import SearchHit, Answerability, GroundedAnswer, Message
from backend.app.rag.answerability import EvidenceGate, REFUSAL
from backend.app.rag.pipeline import RAGPipeline

ID = 'a' * 20


def make_pipeline():
    settings = Settings(_env_file=None, enable_reranker=False)
    hit = SearchHit(chunk_id=ID, source='projects/demo.md', title='DEMO', text='DEMO 使用混合检索。', score=.03, dense_score=.9)
    llm = Mock()
    good = Answerability(answerable=True, reason='直接证据', confidence=.95, evidence_ids=[ID])
    llm.judge_answerability.return_value = good
    llm.verify_answer.return_value = good
    llm.grounded_answer.return_value = GroundedAnswer(answer=f'DEMO 使用混合检索。[{ID}]', evidence_ids=[ID], refused=False)
    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.settings, pipeline.llm = settings, llm
    pipeline.retriever = Mock()
    pipeline.retriever.search.return_value = [hit]
    pipeline.reranker = None
    pipeline.gate = EvidenceGate(settings, llm)
    return pipeline, llm


def test_empty_evidence_never_calls_llm():
    pipeline, llm = make_pipeline()
    pipeline.retriever.search.return_value = []
    result = pipeline.answer([], '候选人参加过 ACM 吗？')
    assert result.refused and result.answer == REFUSAL and result.sources == []
    llm.judge_answerability.assert_not_called()
    llm.grounded_answer.assert_not_called()


def test_insufficient_evidence_never_generates():
    pipeline, llm = make_pipeline()
    llm.judge_answerability.return_value = Answerability(answerable=False, reason='没有比赛记录', confidence=.99)
    assert pipeline.answer([], '候选人参加过 ACM 吗？').refused
    llm.grounded_answer.assert_not_called()


@pytest.mark.parametrize('score', [0.1, None])
def test_dense_threshold_is_independent_of_rrf(score):
    pipeline, llm = make_pipeline()
    pipeline.retriever.search.return_value[0].dense_score = score
    pipeline.retriever.search.return_value[0].score = 999
    assert pipeline.answer([], '问题').refused
    llm.judge_answerability.assert_not_called()


def test_unknown_citation_refused():
    pipeline, llm = make_pipeline()
    llm.grounded_answer.return_value = GroundedAnswer(answer='虚构回答', evidence_ids=['unknown'], refused=False)
    assert pipeline.answer([], '项目是什么？').refused


def test_answer_fact_verification_can_reject():
    pipeline, llm = make_pipeline()
    llm.verify_answer.return_value = Answerability(answerable=False, reason='答案增加了不存在的指标', confidence=.99)
    assert pipeline.answer([], '项目是什么？').refused


def test_multiturn_rewrites_but_generation_only_receives_evidence():
    pipeline, llm = make_pipeline()
    history = [Message(role='assistant', content='不可信的历史断言')]
    llm.rewrite_query.return_value = 'DEMO 为什么使用混合检索？'
    result = pipeline.answer(history, '为什么这么做？')
    assert not result.refused and result.sources[0].chunk_id == ID
    llm.rewrite_query.assert_called_once_with(history, '为什么这么做？')
    pipeline.retriever.search.assert_called_once_with('DEMO 为什么使用混合检索？')
    query, evidence = llm.grounded_answer.call_args.args
    assert query == 'DEMO 为什么使用混合检索？'
    assert all('不可信的历史断言' not in e.text for e in evidence)


def test_reranker_threshold_branch():
    pipeline, llm = make_pipeline()
    pipeline.settings.enable_reranker = True
    evidence = pipeline.retriever.search.return_value
    evidence[0].rerank_score = .01
    assert pipeline.gate.check_answerability('问题', evidence).answerable
    llm.judge_answerability.assert_called_once()
    evidence[0].rerank_score = .9
    assert pipeline.gate.check_answerability('问题', evidence).answerable


def test_unused_valid_declared_citation_does_not_reject_supported_answer():
    pipeline, llm = make_pipeline()
    second = pipeline.retriever.search.return_value[0].model_copy(update={'chunk_id': 'b' * 20})
    pipeline.retriever.search.return_value.append(second)
    llm.judge_answerability.return_value.evidence_ids.append(second.chunk_id)
    llm.grounded_answer.return_value.evidence_ids.append(second.chunk_id)
    llm.verify_answer.return_value = Answerability(answerable=True, reason='有依据', confidence=.95, evidence_ids=[ID])
    result = pipeline.answer([], '具体问题')
    assert not result.refused
    assert [s.chunk_id for s in result.sources] == [ID]


def test_inline_unknown_id_is_rejected():
    pipeline, llm = make_pipeline()
    llm.grounded_answer.return_value.answer = '编造引用[' + 'b' * 20 + ']'
    assert pipeline.answer([], '具体问题').refused


def test_unused_invalid_declared_id_does_not_refuse_supported_career_answer():
    pipeline, llm = make_pipeline()
    pipeline.retriever.search.return_value[0].text = '目前希望寻找大模型应用算法、RAG、Agent 方向的实习。'
    llm.grounded_answer.return_value = GroundedAnswer(
        answer=f'我目前希望找大模型应用算法、RAG 或 Agent 方向的实习。[{ID}]',
        evidence_ids=[ID, '0b3e2e030470fe616f7d7'], refused=False,
    )
    result = pipeline.answer([], '你目前想找什么方向的实习？')
    assert not result.refused
    assert [source.chunk_id for source in result.sources] == [ID]
    _, _, verified = llm.verify_answer.call_args.args
    assert [source.chunk_id for source in verified] == [ID]


def test_unknown_inline_id_still_refused_even_if_declared():
    pipeline, llm = make_pipeline()
    unknown = 'b' * 20
    llm.grounded_answer.return_value = GroundedAnswer(
        answer=f'编造回答。[{unknown}]',
        evidence_ids=[unknown], refused=False,
    )
    assert pipeline.answer([], '不可回答的问题').refused
    llm.verify_answer.assert_not_called()
