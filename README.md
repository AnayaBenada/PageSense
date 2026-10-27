# PageSense

**Ask questions about the web page you're reading.** PageSense is a Chrome extension (side panel) backed by a local retrieval-augmented generation (RAG) pipeline. It reads the current page, finds the passages relevant to your question, and has LLaMA 3.2 answer from those passages only, with clickable citations that highlight the source text on the page.

Everything runs on your machine. Page content never leaves `localhost`.

```
Chrome side panel ──(page text)──▶ FastAPI ──▶ chunk (300 tok, 50 overlap)
        ▲                                      └─▶ embed (all-MiniLM-L6-v2) ──▶ FAISS index (cached per page)
        │                           question ──▶ embed ──▶ FAISS top-20 ──▶ cross-encoder rerank ──▶ top-5
        └──(streamed answer + sources)── LLaMA 3.2 3B via Ollama ◀── grounded prompt with numbered excerpts
```

## Features

- **Side-panel chat**: works on any normal web page; the conversation is kept per URL for the browser session.
- **Grounded answers**: the model is told to answer only from the page and to say "I couldn't find that on this page" otherwise.
- **Clickable citations**: `[1]`-style citations and a source list; clicking one scrolls to and highlights the passage in the page.
- **Streaming**: tokens appear as they're generated (Server-Sent Events).
- **Follow-up questions**: the last 3 turns are sent as history, and the previous question is folded into retrieval.
- **Structure-aware extraction**: keeps headings, lists, tables and code blocks (with indentation); drops nav, footers, cookie banners and hidden elements.
- **Smart Find**: like Ctrl+F, but by meaning. **Alt+Shift+F** (or the magnifier) opens a find bar; type what you're looking for ("refund policy", "how to install") and it highlights the matching sentences on the page, with Enter / Shift+Enter to step through them. No LLM is involved, so results come back in a fraction of a second.
- **Reading library**: ☆ saves a page to a local library (`~/.pagesense/library.db`). The ▤ view lists saved pages and searches passages across all of them; clicking a result opens the page and highlights the passage.
- **Caching**: pages are indexed once per content hash, so asking again is instant; dynamic pages are re-read on every question so answers track what's on screen.

## Setup

Requirements: Python 3.10+, Chrome 116+, [Ollama](https://ollama.com), ~3 GB disk.

**1. Model**

```bash
brew install ollama        # or the installer from ollama.com
brew services start ollama
ollama pull llama3.2
```

**2. Backend**

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

The first start downloads the embedding and reranking models (~200 MB). `curl localhost:8000/health` should report `"status": "ok"`.

**3. Extension**

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and select the `extension/` folder.
3. Pin PageSense, open any article or docs page, and click the icon (or press **Alt+Shift+P**).

Or start Ollama and the backend in one step with `scripts/start.sh`. See [TESTING.md](TESTING.md) for automated checks and a full manual test checklist. New to embeddings, RAG or LLMs? [LEARNING.md](LEARNING.md) walks through every idea PageSense uses, with hands-on experiments on this code.

## Configuration

Backend settings are environment variables (see `backend/app/config.py`):

| Variable | Default | |
|---|---|---|
| `PAGESENSE_LLM_MODEL` | `llama3.2` | Any Ollama model, e.g. `llama3.2:1b` for low-RAM machines |
| `PAGESENSE_EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | |
| `PAGESENSE_RERANK_MODEL` | `cross-encoder/ms-marco-MiniLM-L-12-v2` | Set to empty to disable reranking |
| `PAGESENSE_CHUNK_TOKENS` / `_CHUNK_OVERLAP` | `300` / `50` | |
| `PAGESENSE_TOP_K` | `5` | Excerpts given to the LLM |
| `PAGESENSE_EMBED_DEVICE` | `cpu` | MPS stalls for seconds on small batches on Apple Silicon |
| `PAGESENSE_FIND_CHUNKS` | `2` | Smart Find scores the sentences of this many best chunks |
| `PAGESENSE_FIND_MIN_SCORE` | `-2` | Smart Find hides matches the reranker scores below this |
| `PAGESENSE_LIBRARY_PATH` | `~/.pagesense/library.db` | SQLite file for saved pages. Delete it to clear the library |

The extension's backend URL can be changed in the panel's settings (⚙).

## API

| Endpoint | |
|---|---|
| `POST /index` `{url, title, text}` | Chunk + embed + index a page → `{page_id, n_chunks, n_tokens, index_ms, cached}` |
| `POST /ask` `{page_id, question, history?}` | → `{answer, sources[], timings}` |
| `POST /ask/stream` | Same, as SSE events: `meta` (sources), `token`, `done` (timings), `error` |
| `POST /find` `{page_id, query, top_k?}` | Smart Find → `{matches[{text, then, heading, score}], find_ms}`; `then` is the line a lead-in sentence introduces |
| `GET /health` | Backend, model and Ollama status |
| `POST /library/pages` `{page_id}` | Save an indexed page to the library (replaces an earlier save of the same URL) |
| `GET /library/pages?q=&url=` | Saved pages, newest first; `q` filters by title/URL, `url` finds one |
| `DELETE /library/pages/{id}` | Remove a saved page |
| `POST /library/search` `{query, top_k?}` | Best passages across all saved pages → `{results[{page, heading, text, score}], search_ms}` |

## Evaluation

All numbers below come from scripts in `backend/eval/` and can be reproduced. The dataset is **6 real documentation pages** (snapshots taken with the extension's own extractor, in `eval/pages/`) and **55 questions** (49 answerable, each with a verbatim evidence quote; 6 deliberately not answerable from the page).

| Page | Tokens |
|---|---|
| FastAPI: First Steps | 2,377 |
| Pro Git: Basic Branching and Merging | 2,762 |
| MDN: Using the Fetch API | 5,039 |
| Python tutorial: Data Structures | 6,932 |
| React: `useEffect` reference | 8,106 |
| Wikipedia: Transformer (deep learning) | 20,030 |

### Retrieval accuracy (`python -m eval.eval_retrieval`)

Does one of the retrieved chunks contain the evidence for the answer?

| Setup | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| FAISS only | 55.1% | 87.8% | 93.9% | 0.714 |
| **FAISS top-20 → cross-encoder rerank (default)** | **73.5%** | **91.8%** | **98.0%** | **0.840** |

Chunk-size sweep (FAISS only): 200 tokens → 93.9% hit@5, 300 → 93.9%, 500 → 81.6%. Caveat: the reranker and chunk size were chosen using this same 49-question set, so treat these as optimistic estimates.

### Library search accuracy (`python -m eval.eval_library`)

All 6 pages saved into one library; every question is asked across the whole library instead of its own page.

| Dataset | Right page first | Right page in top 5 | Evidence passage in top 5 | Single-page hit@5 |
|---|---|---|---|---|
| Original (49 q) | 100% | 100% | 98.0% | 98.0% |
| Holdout (21 q) | 100% | 100% | 95.2% | 95.2% |
| Holdout 2 (21 q) | 100% | 100% | 100% | 100% |

Searching the library loses nothing against searching the right page directly (~0.6 s/query, mostly the reranker). Capping results at 2 per page lowered the original set to 91.8%, so results aren't capped. The 6 pages cover unrelated topics, so this doesn't yet test libraries with many similar pages.

### Smart Find accuracy (`python -m eval.eval_find`)

Same questions, typed into Smart Find. **Exact** = the highlighted sentence contains the evidence quote; **visible** = the evidence is within 2 lines of the first highlight, so it's on screen after the jump.

| Dataset | Exact, 1st match | Visible, 1st match | Exact, any of top 3 |
|---|---|---|---|
| Original (49 q) | 51.0% | 75.5% | 71.4% |
| Holdout (21 q) | 42.9% | 61.9% | 52.4% |
| Holdout 2 (21 q) | 61.9% | 76.2% | 76.2% |

~0.65 s per search (default: best 2 chunks, reranked, then every sentence in them scored by the cross-encoder). Without chunk reranking it's ~0.25 s but 10–19 points worse on the holdouts. Questions with no answer on the page scored at most −1.6 (answerable median 4.7–7.4), so matches below −2 are hidden and the bar says "No match". In a live-page check, 131 of 134 top matches were located and highlighted on the real sites; the 3 misses were on a Wikipedia article edited since the snapshot.

### Latency (`python -m eval.bench_latency`)

Apple M3, 8 GB RAM, CPU embeddings, reranking on. Retrieval = embed question + FAISS search + rerank.

| Page size | Chunks | Index (once per page) | Retrieval p50 | Retrieval p95 |
|---|---|---|---|---|
| 2,000 tokens | 11 | 0.12 s | 0.26 s | 0.28 s |
| 5,000 tokens | 28 | 0.29 s | 0.51 s | 0.56 s |
| 10,000 tokens | 54 | 0.58 s | 0.54 s | 0.71 s |
| 20,000 tokens | 98 | 1.05 s | 0.55 s | 0.73 s |

Retrieval stays under 1 s per question up to 20k tokens. The first question on a new page also pays the one-off indexing cost (≈1.6–2.0 s total at 20k tokens). Answer generation dominates end-to-end time; see below.

### Answer quality (`python -m eval.eval_answers`)

Each question is sent through the running backend (`/ask`, reranking on, LLaMA 3.2 3B). Answerable answers are graded against the reference: **2** correct, **1** partially correct, **0** wrong. Grades and notes are in `eval/results/answers.csv`.

| Metric | Result |
|---|---|
| Fully correct answers (grade 2) | **39/49 (79.6%)** |
| Relevant, at least partially correct (grade ≥ 1) | **46/49 (93.9%)** |
| Mean answer score | **86.7%** |
| Out-of-scope questions correctly declined | 6/6 (100%) |
| Answerable questions wrongly declined | 1/49 (2%) |
| Answers that include citations | 45/55 (44 with all citation numbers valid) |
| Retrieval time inside the server | median 1.04 s (higher than the standalone benchmark because the LLM shares the 8 GB of RAM) |
| Generation time | median 5.3 s, max 12.6 s |

The current grades were assigned by Claude (the AI assistant that built this) and should be reviewed by a person before being quoted as a manual evaluation. Typical errors are small-model paraphrase mistakes (e.g. describing Multi-Query Attention as "one attention head") rather than retrieval failures.

### Search-time study (`python -m eval.search_time`)

Not yet run: it needs people timing themselves finding answers manually vs. with PageSense. The protocol is in `backend/eval/search_time.py`.

## Development

```bash
cd backend && .venv/bin/python -m pytest      # unit + API tests (fake embedder/LLM, runs offline)
python scripts/devserver.py                    # snapshot helper + side-panel harness on :8765
```

- `scripts/devserver.py` serves `/harness?page=<snapshot>`, which runs the real side panel in a normal tab with a stubbed `chrome.*` API, and accepts `POST /snapshot` to save page extractions for the eval set.
- `scripts/make_icons.py` regenerates the icons (needs Pillow).

### Notes

- **macOS + faiss + torch**: both wheels bundle `libomp`; `app/__init__.py` sets `KMP_DUPLICATE_LIB_OK` and imports torch first, otherwise the process segfaults.
- **Restricted pages**: Chrome doesn't let extensions read `chrome://` pages, the Web Store, or the built-in PDF viewer.
- **Small model**: LLaMA 3.2 3B occasionally skips citations or phrases answers loosely. A larger Ollama model (e.g. `llama3.1:8b`) improves this if you have the RAM.

## Project layout

```
backend/
  app/        FastAPI app: chunker, embedder + reranker, FAISS index store, RAG prompt, Ollama client
  tests/      pytest suite
  eval/       dataset, page snapshots, eval + benchmark scripts, results/
extension/    Manifest V3 extension: side panel UI, page extractor, background worker
scripts/      dev server / UI harness, icon generator
```
