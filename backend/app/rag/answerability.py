from ..models.schemas import Answerability

REFUSAL = '我目前提供的简历和项目资料中没有足够的信息回答这个问题，因此暂时无法确认。'


def valid_judgement(result, evidence, min_confidence):
    allowed = {e.chunk_id for e in evidence}
    return (
        result.answerable and result.confidence >= min_confidence
        and bool(result.evidence_ids) and set(result.evidence_ids).issubset(allowed)
    )


class EvidenceGate:
    def __init__(self, settings, llm):
        self.settings, self.llm = settings, llm

    def check_answerability(self, query, evidence):
        s = self.settings
        # CrossEncoder scores are ranking signals, not calibrated answerability probabilities.
        # Keep bounded low-score passages for semantic review instead of rejecting facts early.
        eligible = list(evidence) if s.enable_reranker else [
            h for h in evidence if h.dense_score is not None and h.dense_score >= s.min_dense_score
        ]
        if not eligible:
            return Answerability(answerable=False, reason='没有可供核验的证据。', confidence=0.0)
        result = self.llm.judge_answerability(query, eligible)
        if not valid_judgement(result, eligible, s.min_answerability_confidence):
            return Answerability(answerable=False, reason='证据判断未通过：' + result.reason, confidence=0.0)
        return result

