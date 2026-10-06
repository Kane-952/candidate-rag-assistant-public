from pydantic import BaseModel, Field, ConfigDict, field_validator


class Chunk(BaseModel):
    source: str
    title: str
    chunk_id: str
    text: str


class SearchHit(Chunk):
    score: float
    bm25_score: float | None = None
    dense_score: float | None = None
    rerank_score: float | None = None


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100, pattern=r'^[\w-]+$')
    message: str = Field(min_length=1, max_length=2000)

    @field_validator('message')
    @classmethod
    def strip_message(cls, value):
        if not value.strip():
            raise ValueError('消息不能为空')
        return value.strip()


class ChatResponse(BaseModel):
    answer_id: str | None = None
    answer: str
    sources: list[Chunk] = Field(default_factory=list)
    refused: bool


class Answerability(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    answerable: bool
    reason: str = Field(max_length=1000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    answer: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str]
    refused: bool
