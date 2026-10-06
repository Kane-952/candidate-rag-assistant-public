from ..models.schemas import SearchHit


def reciprocal_rank_fusion(rankings: list[list[SearchHit]], rrf_k: int = 60) -> list[SearchHit]:
    if rrf_k < 1:
        raise ValueError('rrf_k must be positive')
    fused = {}
    for ranking in rankings:
        seen = set()
        for rank, hit in enumerate(ranking, start=1):
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            if hit.chunk_id not in fused:
                fused[hit.chunk_id] = hit.model_copy(update={'score': 0.0})
            result = fused[hit.chunk_id]
            result.score += 1 / (rrf_k + rank)
            if hit.bm25_score is not None:
                result.bm25_score = hit.bm25_score
            if hit.dense_score is not None:
                result.dense_score = hit.dense_score
    return sorted(fused.values(), key=lambda h: (-h.score, h.chunk_id))


class HybridRetriever:
    def __init__(self, bm25, dense, settings):
        self.bm25, self.dense, self.settings = bm25, dense, settings

    def search(self, query):
        s = self.settings
        rankings = [self.bm25.search(query, s.bm25_top_k), self.dense.search(query, s.dense_top_k)]
        return reciprocal_rank_fusion(rankings, s.rrf_k)[:s.hybrid_top_k]
