import pytest
from backend.app.models.schemas import SearchHit
from backend.app.rag.hybrid import reciprocal_rank_fusion
from backend.app.rag.bm25 import BM25Retriever
from backend.app.models.schemas import Chunk


def hit(name, **scores):
    return SearchHit(chunk_id=name, title=name, text=name, source='test.md', score=1, **scores)


def test_rrf_uses_rank_and_preserves_original_scores():
    bm25 = [hit('a', bm25_score=12), hit('b', bm25_score=7)]
    dense = [hit('b', dense_score=0.8), hit('c', dense_score=0.7)]
    result = reciprocal_rank_fusion([bm25, dense], 60)
    assert result[0].chunk_id == 'b'
    assert result[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result[0].bm25_score == 7 and result[0].dense_score == 0.8
    assert bm25[1].score == 1


def test_duplicate_does_not_double_count():
    result = reciprocal_rank_fusion([[hit('a'), hit('a')]])
    assert result[0].score == pytest.approx(1 / 61)


def test_bm25_chinese_english_and_no_match():
    docs = [Chunk(chunk_id='a', source='a.md', title='混合检索', text='项目采用 BM25 和向量检索。'),
            Chunk(chunk_id='b', source='b.md', title='背景', text='候选人毕业于示例大学。')]
    retriever = BM25Retriever(docs)
    assert retriever.search('BM25 混合检索', 1)[0].chunk_id == 'a'
    assert retriever.search('zzzzzz', 5) == []
