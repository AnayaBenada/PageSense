import json
import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.study import MIN_QUIZ_CHUNK_TOKENS, _prose_tokens, CHECK_CHOICE_SYSTEM, CHECK_OPEN_SYSTEM, notes_chunks, parse_question, quiz_chunks, shuffle_choices
from tests.conftest import FakeEmbedder, FakeLLM

PAGE = {
    "url": "https://shop.example.com/terms",
    "title": "Terms of Service",
    "text": (
        "# Refunds\n\nYou can return any item within 30 days for a full refund. Refunds go back to the original payment method. "
        "Gift cards and opened software can't be returned, and shipping costs are not refunded on returns.\n\n"
        "# Shipping\n\nOrders ship within two business days. Tracking numbers are emailed once the parcel leaves the warehouse. "
        "Express delivery is available in most regions for an extra fee charged at checkout.\n\n"
    ),
}
QUESTION = {
    "question": "How long do you have to return an item for a full refund?",
    "choices": ["30 days", "14 days", "60 days", "90 days"],
    "answer": 0,
    "quote": "You can return any item within 30 days for a full refund.",
}


class QuizLLM(FakeLLM):
    """Writes QUESTION, then answers the blind checks like a student who knows it's 30 days
    (or, with always_a=True, like a model that always picks choice A)."""

    def __init__(self, always_a=False):
        super().__init__()
        self.always_a = always_a

    def json_reply(self, messages):
        system, user = messages[0]["content"], messages[-1]["content"]
        if system == CHECK_CHOICE_SYSTEM:
            letter = "A" if self.always_a else re.search(r"^([A-D])\) 30 days$", user, re.M).group(1)
            return [json.dumps({"answer": letter})]
        if system == CHECK_OPEN_SYSTEM:
            return [json.dumps({"answer": "within 30 days"})]
        return [json.dumps(QUESTION)]


def sse(text):
    for block in text.strip().split("\n\n"):
        event = re.search(r"^event: (.*)$", block, re.M).group(1)
        yield event, json.loads(re.search(r"^data: (.*)$", block, re.M).group(1))


def study_client(llm, tmp_path):
    cfg = Settings(chunk_tokens=40, chunk_overlap=5, library_path=str(tmp_path / "lib.db"), quiz_questions=1)
    return TestClient(create_app(cfg, embedder=FakeEmbedder(), llm=llm))


def test_parse_question_rejects_unusable_replies():
    good = json.dumps(QUESTION)
    assert parse_question(good)["choices"][0] == "30 days"
    assert parse_question(json.dumps({**QUESTION, "answer": "2"}))["answer"] == 2
    assert parse_question("not json") is None
    assert parse_question(json.dumps({**QUESTION, "choices": ["a", "b", "c"]})) is None
    assert parse_question(json.dumps({**QUESTION, "choices": ["30 days", "30 Days", "60 days", "90 days"]})) is None
    assert parse_question(json.dumps({**QUESTION, "choices": ["30 days", "14 days", "60 days", "All of the above"]})) is None
    assert parse_question(json.dumps({**QUESTION, "answer": 4})) is None


def test_shuffle_keeps_the_right_answer():
    for seed in range(10):
        q = shuffle_choices(QUESTION, str(seed))
        assert q["choices"][q["answer"]] == "30 days" and sorted(q["choices"]) == sorted(QUESTION["choices"])


def test_quiz_streams_checked_questions_with_evidence(tmp_path):
    with study_client(QuizLLM(), tmp_path) as c:
        events = list(sse(c.post("/study/quiz", json=PAGE).text))
    questions = [d for e, d in events if e == "question"]
    assert len(questions) == 1 and events[-1][0] == "done"
    q = questions[0]
    assert q["choices"][q["answer"]] == "30 days"
    assert q["evidence"] == "You can return any item within 30 days for a full refund."


def test_quiz_drops_questions_that_fail_the_blind_check(tmp_path):
    # A checker that always says "A" can't agree with the key in both orders.
    with study_client(QuizLLM(always_a=True), tmp_path) as c:
        events = list(sse(c.post("/study/quiz", json=PAGE).text))
    assert [e for e, _ in events] == ["error"] and events[0][1]["code"] == "quiz_failed"


def test_notes_stream_and_cover_long_pages_evenly(client):
    events = list(sse(client.post("/study/notes", json=PAGE).text))
    assert events[0] == ("meta", {"page_id": events[0][1]["page_id"], "partial": False})
    assert "".join(d["t"] for e, d in events if e == "token") == "The answer [1]"

    long_text = "\n\n".join(f"# Part {i}\n\n" + " ".join(f"word{i}x{j}" for j in range(200)) for i in range(40))
    page = client.app.state.store.add("https://x.test/long", "Long", long_text)[0]
    picked = notes_chunks(page, budget=1000)
    assert sum(ch.n_tokens for ch in picked) <= 1000
    assert picked[0].id == 0 and picked[-1].id > len(page.chunks) // 2  # spread through the page


def test_quiz_chunks_skip_code_listings(client):
    text = "# Intro\n\n" + " ".join(f"prose{i}" for i in range(120)) + "\n\n```\n" + "\n".join(f"code_line_{i} = {i}" for i in range(200)) + "\n```"
    page = client.app.state.store.add("https://x.test/code", "Code", text)[0]
    code_only = [ch for ch in page.chunks if _prose_tokens(ch) < MIN_QUIZ_CHUNK_TOKENS]
    assert code_only  # the page really has chunks to skip
    assert all(_prose_tokens(ch) >= MIN_QUIZ_CHUNK_TOKENS for ch in quiz_chunks(page, 3))
