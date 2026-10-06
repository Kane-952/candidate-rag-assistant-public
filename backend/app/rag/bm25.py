import math
import re
from collections import Counter

from ..models.schemas import Chunk, SearchHit


def tokenize(text: str) -> list[str]:
    # 英文词 + 中文单字/双字，避免依赖大型分词词典。
    tokens = []
    for word in re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', text.lower()):
        if '\u4e00' <= word[0] <= '\u9fff':
            tokens.extend(word)
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


class BM25Retriever:
    def __init__(self, chunks: list[Chunk], k1=1.5, b=0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.counts = [Counter(tokenize(c.title + '\n' + c.text)) for c in chunks]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.avg_length = sum(self.lengths) / max(len(chunks), 1) or 1
        self.df = Counter(t for counts in self.counts for t in counts)

    def search(self, query: str, top_k: int) -> list[SearchHit]:
        hits = []
        for chunk, counts, length in zip(self.chunks, self.counts, self.lengths):
            score = 0.0
            for term in set(tokenize(query)):
                tf = counts[term]
                if not tf:
                    continue
                idf = math.log(1 + (len(self.chunks) - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.avg_length))
            if score > 0:
                hits.append(SearchHit(**chunk.model_dump(), score=score, bm25_score=score))
        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]
