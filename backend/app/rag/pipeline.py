import logging
import re
import time

from ..errors import IndexUnavailable
from ..models.schemas import ChatResponse, Chunk
from .answerability import EvidenceGate, REFUSAL, valid_judgement
from .bm25 import BM25Retriever
from .chunking import read_chunks, load_knowledge_base
from .dense import DenseRetriever
from .hybrid import HybridRetriever
from .query_rewrite import rewrite_query
from .reranker import Reranker
from .context_selection import select_context
from ..perf import current_trace, llm_phase, observe, timed
from ..request_control import check_cancelled

logger = logging.getLogger(__name__)


class RAGPipeline:
    def __init__(self, settings, llm):
        self.settings, self.llm = settings, llm
        with timed('knowledge_base_load'):
            try:
                chunks = read_chunks(settings.index_dir / 'chunks.jsonl')
            except (OSError, ValueError):
                raise IndexUnavailable('索引不存在或不可读，请先运行 python scripts/build_index.py。') from None
            if not chunks:
                raise IndexUnavailable('知识库为空，请添加 Markdown 资料并重建索引。')
            current = load_knowledge_base(settings.kb_dir, settings.chunk_size, settings.chunk_overlap)
            if current != chunks:
                raise IndexUnavailable('知识库或分块配置已变更，请重建索引并重启服务。')
        self.chunks = chunks
        dense = DenseRetriever(chunks, settings)
        with timed('index_load'):
            dense.load(settings.index_dir)
        with timed('retriever_init'):
            self.retriever = HybridRetriever(BM25Retriever(chunks, settings.bm25_k1, settings.bm25_b), dense, settings)
        self.reranker = Reranker(settings) if settings.enable_reranker else None
        self.gate = EvidenceGate(settings, llm)

    def answer(self, history, question, on_text=None):
        started = time.perf_counter()
        try:
            check_cancelled()
            with llm_phase('rewrite'):
                query = rewrite_query(history, question, self.llm)
            observe('rewritten_query', query)
            check_cancelled()
            with timed('retrieval_total'):
                retrieved = self.retriever.search(query)
            observe('retrieved', lambda: [hit.model_dump() for hit in retrieved])
            logger.info('retrieved_chunk_ids=%s', [h.chunk_id for h in retrieved])
            check_cancelled()
            with timed('reranker_total'):
                evidence = (self.reranker.rerank(query, retrieved) if self.reranker
                            else retrieved[:self.settings.rerank_top_k])
            observe('reranked', lambda: [hit.model_dump() for hit in evidence])
            with timed('context_selection'):
                evidence = select_context(query, evidence, getattr(self, 'chunks', []))
            observe('context_candidates', lambda: [hit.model_dump() for hit in evidence])
            logger.info('reranked_chunk_ids=%s', [h.chunk_id for h in evidence])
            check_cancelled()
            with llm_phase('judge'), timed('answerability_total'):
                decision = self.gate.check_answerability(query, evidence)
            observe('answerability', decision.model_dump)
            check_cancelled()
            logger.info('answerable=%s', decision.answerable)
            if not decision.answerable:
                observe('decision', {'status': 'REFUSE', 'reason': decision.reason})
                return ChatResponse(answer=REFUSAL, refused=True)
            evidence = [h for h in evidence if h.chunk_id in decision.evidence_ids]
            observe('final_context', lambda: [hit.model_dump() for hit in evidence])
            with llm_phase('answer'), timed('generation_total'):
                generated = (self.llm.grounded_answer(query, evidence, on_text=lambda _: check_cancelled())
                             if on_text is not None else self.llm.grounded_answer(query, evidence))
            observe('generation', generated.model_dump)
            check_cancelled()
            allowed = {h.chunk_id for h in evidence}
            cited = set(generated.evidence_ids)
            inline_ids = set(re.findall(r'\[([0-9a-f]{20})\]', generated.answer))
            logger.info('generation_refused=%s', generated.refused)
            # A typo in an unused metadata ID is not a citation in the answer.
            # Every visible citation must still be declared, belong to Context, and pass fact verification.
            if (generated.refused or not cited or not inline_ids
                    or not inline_ids.issubset(cited)
                    or not inline_ids.issubset(allowed)):
                logger.info("citation_validation_failed=True")
                reason = (
                    '模型生成阶段标记拒答。' if generated.refused else
                    '模型未声明引用证据。' if not cited else
                    '回答正文缺少有效引用。' if not inline_ids else
                    '回答正文引用与模型声明的证据不一致。' if not inline_ids.issubset(cited) else
                    '回答正文引用了未提供给生成阶段的证据。'
                )
                observe('decision', {'status': 'REFUSE', 'reason': reason})
                return ChatResponse(answer=REFUSAL, refused=True)
            used = [h for h in evidence if h.chunk_id in inline_ids]
            with llm_phase('verify'), timed('verification_total'):
                verification = self.llm.verify_answer(query, generated.answer, used)
            observe('verification', verification.model_dump)
            check_cancelled()
            logger.info('verification_answerable=%s', verification.answerable)
            if not valid_judgement(verification, used, self.settings.min_answerability_confidence):
                observe('decision', {'status': 'REFUSE', 'reason': '最终回答核验未通过：' + verification.reason})
                return ChatResponse(answer=REFUSAL, refused=True)
            observe('decision', {'status': 'ANSWER', 'reason': '证据判断、引用校验与回答核验通过。'})
            if on_text is not None:
                on_text(generated.answer)
            return ChatResponse(
                answer=generated.answer, refused=False,
                sources=[Chunk(**h.model_dump(include=set(Chunk.model_fields))) for h in used],
            )
        finally:
            trace = current_trace()
            if trace:
                trace.mark('answer_pipeline_end_ms')
            logger.info('latency_seconds=%.3f', time.perf_counter() - started)

