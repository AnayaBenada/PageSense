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


class FindRequest(BaseModel):
    query: str
    page_id: str | None = None
    # Alternative to page_id: send the page and index it inline.
    url: str | None = None
    title: str = ""
    text: str | None = None
    top_k: int | None = Field(None, ge=1, le=20)


class FindMatch(BaseModel):
    text: str
    then: str  # the line after `text` when `text` introduces it, else ""
    heading: str
    chunk_id: int
    score: float


class FindResponse(BaseModel):
    page_id: str
    matches: list[FindMatch]
    find_ms: float


class PageRef(BaseModel):
    page_id: str | None = None
    # Alternative to page_id: send the page and index it inline.
    url: str | None = None
    title: str = ""
    text: str | None = None


class QuizRequest(PageRef):
    n: int | None = Field(None, ge=1, le=10)
    # 0 = questions spread evenly through the page; 1, 2, … = a different set each time.
    round: int = Field(0, ge=0)


class NotesRequest(PageRef):
    pass


class SaveRequest(BaseModel):
    page_id: str


class SavedPageOut(BaseModel):
    id: int
    url: str
    title: str
    page_id: str
    saved_at: float
    n_chunks: int
    n_tokens: int


class LibraryPagesResponse(BaseModel):
    pages: list[SavedPageOut]


class LibrarySearchRequest(BaseModel):
    query: str
    top_k: int | None = Field(None, ge=1, le=20)


class LibraryResult(BaseModel):
    page: SavedPageOut
    chunk_id: int
    heading: str
    text: str
    score: float


class LibrarySearchResponse(BaseModel):
    results: list[LibraryResult]
    search_ms: float
