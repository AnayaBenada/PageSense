# Learning PageSense

A guide to the ideas PageSense is built on, using PageSense's own code. Each lesson has the idea in plain words, where it lives in this project, something to try (about 10 minutes), and where to learn more.

Work through them in order; each one builds on the last.

---

## The big picture: what happens when you ask a question

```
1. The extension reads the page's text           extension/extractor.js
2. The text is cut into ~300-token pieces        backend/app/chunker.py        (Lesson 1)
3. Each piece is turned into a list of numbers   backend/app/embedder.py       (Lesson 2)
4. Your question becomes numbers too, and the
   closest pieces are found                      backend/app/index_store.py    (Lesson 3)
5. A second model re-checks the top 20           backend/app/embedder.py       (Lesson 4)
6. The best 5 pieces + your question go to
   LLaMA, which writes the answer                backend/app/rag.py, llm.py    (Lesson 5)
```

That pattern (search first, then let a language model write from what was found) is called **RAG: retrieval-augmented generation**. Smart Find (`find.py`) is steps 1–5 with no writing step, which is why it's fast. The library (`library.py`) is steps 3–5 across every saved page.

Before running anything below:

```bash
cd ~/PageSense/backend
source .venv/bin/activate
```

---

## Lesson 1: Chunking

**The idea.** Models can only read a limited amount of text at once, and searching works better on small pieces than on a whole page. So the page is cut into **chunks** of about 300 **tokens** (a token is roughly ¾ of a word). Neighbouring chunks overlap by 50 tokens so a sentence cut at the edge still appears whole in one of them.

**In PageSense.** `backend/app/chunker.py`. It's short and commented. Notice it tries to cut at paragraph breaks and remembers which heading each chunk sits under.

**Try it.**

```python
import json
from app.chunker import chunk_text, count_tokens
text = json.load(open("eval/pages/fastapi_first_steps.json"))["text"]
print(count_tokens("Hello, how are you today?"))       # how many tokens is a sentence?
chunks = chunk_text(text, 300, 50)
print(len(chunks), "chunks")
print(chunks[3].heading, "\n", chunks[3].text[:400])
```

**Question to think about.** Why might 500-token chunks find answers *less* often than 300? (Lesson 6 has the answer.)

---

## Lesson 2: Embeddings (the core idea)

**The idea.** An **embedding model** turns a piece of text into a list of numbers (here, 384 of them) so that texts with similar *meaning* get similar numbers, even if they share no words. Comparing two lists gives a **similarity score**: about 1 means the same meaning, about 0 means unrelated. This is why PageSense finds "end my plan" when you search "cancel my subscription".

**In PageSense.** `backend/app/embedder.py` uses `all-MiniLM-L6-v2`, a small, fast model from the `sentence-transformers` library.

**Try it.**

```python
import app
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
texts = ["cancel my subscription", "end my plan", "how to bake bread"]
vecs = model.encode(texts, normalize_embeddings=True)
print(vecs.shape)          # (3, 384): 3 texts, 384 numbers each
print(vecs[0][:5])         # the first 5 numbers for "cancel my subscription"
print(vecs[0] @ vecs[1])   # vs "end my plan"
print(vecs[0] @ vecs[2])   # vs "how to bake bread"
```

You should see about **0.40** for "end my plan" and about **0.00** for "how to bake bread". The two share no words, yet the model knows they mean nearly the same thing. Try your own sentences.

**Learn more.** sbert.net, the documentation for the `sentence-transformers` library this project uses. Start with "Quickstart" and "Semantic Search".

---

## Lesson 3: Vector search with FAISS

**The idea.** Once every chunk is a list of numbers (a **vector**), "find the chunks most related to my question" just means "find the vectors closest to the question's vector". **FAISS** is a library from Meta that does this very fast.

**In PageSense.**
- `backend/app/index_store.py` keeps one FAISS index per page you ask about (in memory, newest 20 pages).
- `backend/app/library.py` keeps one FAISS index over *every* saved page, rebuilt at startup from a small database file (SQLite) at `~/.pagesense/library.db`.

**Try it.**

```python
import app, faiss, numpy as np
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
docs = ["Refunds are issued within 30 days.", "Orders ship in two days.", "Our office is in Berlin."]
index = faiss.IndexFlatIP(384)                          # "IP" = inner product = similarity
index.add(model.encode(docs, normalize_embeddings=True))
scores, ids = index.search(model.encode(["can I get my money back?"], normalize_embeddings=True), 2)
print([(docs[i], round(float(s), 2)) for s, i in zip(scores[0], ids[0])])
```

**Learn more.** The FAISS wiki (github.com/facebookresearch/faiss/wiki), "Getting started". Pinecone's free "Learn" articles explain vector search well without code.

---

## Lesson 4: Reranking with a cross-encoder

**The idea.** Embeddings are fast but rough: each text is turned into numbers *on its own*, before the model sees the question. A **cross-encoder** reads the question and a passage *together*, so it judges relevance much better, but it's too slow to run on every chunk. So PageSense uses both: FAISS picks the top 20 quickly, then the cross-encoder **reranks** those 20.

Smart Find goes one step further: it reranks chunks, then splits the best 2 into sentences and has the cross-encoder score every sentence. That's how it highlights one sentence instead of a whole chunk.

**In PageSense.** `CrossEncoderReranker` in `backend/app/embedder.py`; used in `rag.py` (`retrieve`) and `find.py`.

**Try it.**

```python
import app
from sentence_transformers import CrossEncoder
ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
q = "how do I get my money back?"
cands = ["You can return any item within 30 days for a full refund.",
         "Orders ship within two business days.",
         "Money is a medium of exchange."]
for c, s in zip(cands, ce.predict([(q, c) for c in cands])):
    print(f"{s:6.2f}  {c}")
```

The refund sentence wins (about −5.2) even though it shares no words with the question, and "Money is a medium of exchange" loses (about −7.4) even though it contains "money". The scores aren't percentages; only the ranking matters.

Then see what reranking is worth on real pages:

```bash
python -m eval.eval_retrieval              # with reranker:    hit@5 = 98.0%
python -m eval.eval_retrieval --no-rerank  # FAISS only:       hit@5 = 93.9%
```

**Learn more.** sbert.net → "Cross-Encoders" and "Retrieve & Re-Rank".

---

## Lesson 5: The language model and the prompt

**The idea.** A **large language model (LLM)** predicts text one token at a time. PageSense uses **LLaMA 3.2 (3B)**, a small open model from Meta, run on your own computer by **Ollama**. The model doesn't know your page; it only sees what's in the **prompt**: a set of rules, the 5 best chunks numbered [1]–[5], and your question. Grounding the answer in retrieved text like this is what keeps it from making things up.

**In PageSense.**
- `backend/app/rag.py`: read `SYSTEM_PROMPT` at the top. Those plain-English rules ("Answer ONLY using the numbered page excerpts", "cite like [2]") are the whole instruction to the model.
- `backend/app/llm.py`: sends the prompt to Ollama and **streams** the reply back token by token.
- **Temperature** (`PAGESENSE_LLM_TEMPERATURE`, default 0) controls randomness. 0 means the same answer every time.

**Try it.**
1. Talk to the model directly with no page: `ollama run llama3.2` then ask "What's in the FastAPI tutorial?" Notice it answers from memory, possibly wrongly.
2. In `rag.py`, delete the line `- Cite the excerpts you used with their numbers in square brackets`, restart the backend, and ask a question. Do citations disappear? (Put it back after.)
3. Ask the panel something the page doesn't cover. It should say "I couldn't find that on this page." Find the rule that makes it do that.

**Learn more.**
- Andrej Karpathy, "Intro to Large Language Models" (YouTube, ~1 hour). The best plain-language overview.
- 3Blue1Brown's neural network series on YouTube, the chapters on transformers and attention, for how these models work inside.
- Jay Alammar, "The Illustrated Transformer" (jalammar.github.io), a picture-by-picture explanation.
- Later, if you want to build one: Karpathy's "Let's build GPT: from scratch, in code, spelled out".

---

## Lesson 6: Evaluation (how we decide what's better)

**The idea.** "It seems to work" isn't enough; every choice in PageSense was made by measuring. `backend/eval/` holds 6 real documentation pages and about 90 questions, each with an **evidence quote**: the exact words on the page that answer it. The scripts check whether the right text was found.

- **hit@5**: was the evidence in one of the top 5 results?
- **Holdout sets** (`dataset_holdout*.jsonl`) were written *after* tuning, to check the choices aren't just fitted to the first set of questions. That's why a change that only helps the original set gets rejected (it happened with Smart Find, see the README).

**Try it.**

```bash
python -m eval.eval_retrieval --chunk-tokens 500 --overlap 80 --no-rerank   # bigger chunks: worse (81.6% vs 93.9%)
python -m eval.eval_retrieval --dataset dataset_holdout                     # the questions written after tuning
python -m eval.eval_find                                                    # Smart Find accuracy
python -m eval.eval_library                                                 # search across a whole library
```

*Answer to Lesson 1's question:* a big chunk mixes several topics, so its single list of numbers is a blurry average of all of them and matches any one question less closely.

**Exercise.** Write 5 questions of your own about one of the pages in `eval/pages/`, in the same format as `dataset.jsonl`, and run the evals on them.

---

## Glossary

| Term | Meaning |
|---|---|
| Token | A piece of a word; the unit models read. ~¾ of an English word |
| Chunk | A ~300-token piece of a page |
| Embedding / vector | A list of numbers representing a text's meaning |
| Cosine similarity | How close two embeddings are (≈1 same meaning, ≈0 unrelated) |
| FAISS | Library for fast "find the closest vectors" search |
| BM25 | Classic keyword search (exact word matches). In `lexical.py`; off by default because it didn't help |
| Cross-encoder / reranker | Model that scores a question + passage together; slower but more accurate |
| LLM | Large language model; writes text one token at a time |
| Prompt | Everything the LLM is given: rules + page excerpts + question |
| RAG | Retrieval-augmented generation: search first, then write from what was found |
| Temperature | Randomness of the LLM's output; 0 means always the same |
| Streaming | Sending the answer token by token as it's written (Server-Sent Events) |
| hit@k | Share of questions where the right text was in the top k results |
| Holdout set | Test questions kept aside so you don't fool yourself while tuning |

---

## Keep learning as the project grows

Whenever a new feature lands, ask Claude: **"explain what we just built like I'm new to this, using our code."** Add a lesson here for anything new.
