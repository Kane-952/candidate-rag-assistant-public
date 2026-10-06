import hashlib
import json

import numpy as np

from ..errors import IndexUnavailable, ModelUnavailable
from ..models.schemas import SearchHit
from ..perf import timed


class DenseRetriever:
    def __init__(self, chunks, settings):
        self.chunks, self.settings = chunks, settings
        self.model = None
        self.index = None

    def _load_model(self):
        if self.model is None:
            try:
                with timed('embedding_model_load'):
                    from sentence_transformers import SentenceTransformer
                    self.model = SentenceTransformer(self.settings.embedding_model, device=self.settings.model_device)
                    self.model.max_seq_length = self.settings.model_max_length
            except Exception:
                raise ModelUnavailable('Embedding 模型加载失败，请检查模型下载、缓存和 MODEL_DEVICE。') from None
        return self.model

    def encode(self, texts):
        try:
            vectors = self._load_model().encode(
                texts, batch_size=self.settings.model_batch_size,
                normalize_embeddings=True, show_progress_bar=False,
            )
            return np.ascontiguousarray(vectors, dtype=np.float32)
        except ModelUnavailable:
            raise
        except Exception:
            raise ModelUnavailable('Embedding 推理失败，请检查模型和本机资源。') from None

    def fingerprint(self):
        payload = [c.model_dump() for c in self.chunks]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def build(self, directory):
        import faiss
        vectors = self.encode([c.title + '\n' + c.text for c in self.chunks])
        self.index = faiss.IndexFlatIP(vectors.shape[1])  # 归一化内积 = cosine similarity
        self.index.add(vectors)
        directory.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(directory / 'vectors.faiss'))
        manifest = {
            'embedding_model': self.settings.embedding_model,
            'max_length': self.settings.model_max_length,
            'chunk_fingerprint': self.fingerprint(),
            'dimension': vectors.shape[1],
            'count': len(self.chunks),
        }
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

    def load(self, directory):
        try:
            import faiss
            manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
            if (manifest['embedding_model'] != self.settings.embedding_model
                    or manifest['max_length'] != self.settings.model_max_length
                    or manifest['chunk_fingerprint'] != self.fingerprint()):
                raise ValueError('Incompatible index')
            self.index = faiss.read_index(str(directory / 'vectors.faiss'))
            if self.index.ntotal != len(self.chunks) or self.index.d != manifest['dimension']:
                raise ValueError('Corrupt index')
        except Exception:
            raise IndexUnavailable('索引缺失、损坏或配置不匹配，请运行 python scripts/build_index.py。') from None

    def search(self, query, top_k):
        if self.index is None:
            raise IndexUnavailable('向量索引尚未加载。')
        with timed('query_embedding'):
            vectors = self.encode([self.settings.embedding_query_prefix + query])
        if vectors.shape[1] != self.index.d:
            raise IndexUnavailable('Embedding 维度已改变，请重建索引。')
        scores, indices = self.index.search(vectors, min(top_k, len(self.chunks)))
        return [
            SearchHit(**self.chunks[int(i)].model_dump(), score=float(score), dense_score=float(score))
            for score, i in zip(scores[0], indices[0]) if i >= 0
        ]
