from ..errors import ModelUnavailable
from ..perf import timed


class Reranker:
    def __init__(self, settings):
        self.settings = settings
        self.model = None

    def _load_model(self):
        if self.model is None:
            try:
                with timed('reranker_model_load'):
                    from sentence_transformers import CrossEncoder
                    self.model = CrossEncoder(
                        self.settings.reranker_model, device=self.settings.model_device,
                        max_length=self.settings.model_max_length,
                    )
            except Exception:
                raise ModelUnavailable('Reranker 加载失败；检查模型下载，或设置 ENABLE_RERANKER=false 后重启。') from None
        return self.model

    def rerank(self, query, hits):
        if not hits:
            return []
        self._load_model()
        try:
            import torch
            # 显式 sigmoid，把单 logit 转成 0..1 分数；这不是校准后的概率。
            scores = self.model.predict(
                [(query, h.title + '\n' + h.text) for h in hits],
                batch_size=self.settings.model_batch_size,
                activation_fn=torch.nn.Sigmoid(), show_progress_bar=False,
            )
            result = [h.model_copy(update={'rerank_score': float(s)}) for h, s in zip(hits, scores)]
            return sorted(result, key=lambda h: h.rerank_score, reverse=True)[:self.settings.rerank_top_k]
        except Exception:
            raise ModelUnavailable('Reranker 推理失败，请检查模型和本机资源。') from None
