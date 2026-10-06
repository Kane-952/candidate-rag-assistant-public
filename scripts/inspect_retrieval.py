"""Real CPU retrieval smoke check. Does not call a paid LLM API."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import get_settings
from backend.app.rag.chunking import read_chunks
from backend.app.rag.bm25 import BM25Retriever
from backend.app.rag.dense import DenseRetriever
from backend.app.rag.hybrid import HybridRetriever
from backend.app.rag.reranker import Reranker


def main():
    settings = get_settings()
    chunks = read_chunks(settings.index_dir / 'chunks.jsonl')
    dense = DenseRetriever(chunks, settings)
    dense.load(settings.index_dir)
    hybrid = HybridRetriever(BM25Retriever(chunks, settings.bm25_k1, settings.bm25_b), dense, settings)
    reranker = Reranker(settings) if settings.enable_reranker else None
    for query in ['DEMO 候选人做过什么项目？', 'DEMO 项目为什么采用混合检索？', '候选人参加过 ACM 吗？']:
        hits = hybrid.search(query)
        if reranker:
            hits = reranker.rerank(query, hits)
        print(json.dumps({'query': query, 'hits': [
            {'source': h.source, 'chunk_id': h.chunk_id, 'dense': h.dense_score, 'rrf': h.score, 'rerank': h.rerank_score}
            for h in hits
        ]}, ensure_ascii=False))


if __name__ == '__main__':
    main()

