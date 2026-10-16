from pydantic import BaseModel, Field


class IndexRequest(BaseModel):
    url: str = Field(..., max_length=4096)
    title: str = Field("", max_length=1000)
    text: str


class IndexResponse(BaseModel):
    page_id: str
    n_chunks: int
    n_tokens: int
    index_ms: float
    cached: bool


class Turn(BaseModel):
    question: str
    answer: str


class AskRequest(BaseModel):
    question: str
    page_id: str | None = None
    # Alternative to page_id: send the page and index it inline.
    url: str | None = None
    title: str = ""
    text: str | None = None
    history: list[Turn] = Field(default_factory=list)
    top_k: int | None = Field(None, ge=1, le=20)


class Source(BaseModel):
    chunk_id: int
    heading: str
    text: str
    score: float | None = None


class Timings(BaseModel):
    index_ms: float = 0.0
    retrieve_ms: float
    generate_ms: float = 0.0
    total_ms: float = 0.0


class AskResponse(BaseModel):
    page_id: str
    answer: str
    sources: list[Source]
    timings: Timings
