# Testing PageSense

## 1. Start it

```bash
cd ~/PageSense
scripts/start.sh
```

This starts Ollama (pulling `llama3.2` if needed) and the backend on `http://localhost:8000`. Leave this terminal open; Ctrl+C stops the backend.

Load the extension once:
1. Go to `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose `~/PageSense/extension`.
3. Pin PageSense from the puzzle-piece menu.

After editing extension files, click the ↻ on the PageSense card in `chrome://extensions` and reopen the side panel.

## 2. Automated checks

Run these in a second terminal with the backend running:

```bash
cd ~/PageSense
backend/.venv/bin/python -m pytest backend -q        # 24 unit/API tests, offline
backend/.venv/bin/python scripts/smoke_test.py        # 25 checks against the live backend + LLM
```

`smoke_test.py` covers: health, indexing, caching, empty and oversized pages, malformed requests, unknown page IDs, blank and over-long questions, answerable/unanswerable/follow-up questions, streaming, and CORS.

## 3. Manual checks in Chrome

Open the panel with the toolbar icon or **Alt+Shift+P**. The dot next to "PageSense" is green when ready, amber when the backend is up but the LLM isn't, and red when the backend is unreachable. ⚙ shows details.

### Normal use

| # | Do this | Expect |
|---|---|---|
| 1 | Open https://fastapi.tiangolo.com/tutorial/first-steps/ and ask "What URL serves the interactive docs?" | "Reading the page…" → "Searching…" → a streamed answer with `/docs` and Swagger UI. The first answer after startup can take ~20 s while the model loads |
| 2 | Follow up: "And the alternative ones?" | Answer about ReDoc at `/redoc` (history works) |
| 3 | Click a citation chip like `1` | The page scrolls to the passage and highlights it yellow for ~8 s |
| 4 | Expand "N sources from this page" and click one | Same scroll and highlight |
| 5 | Ask "How do I deploy to AWS Lambda?" | "I couldn't find that on this page." |
| 6 | Click a suggestion chip ("Summarize this page") | Asks it and answers |
| 7 | Switch to another tab, then back | Each tab/URL shows its own conversation |
| 8 | Close and reopen the side panel | The conversation is still there (kept until Chrome restarts) |
| 9 | Click ⌫ (clear) | The conversation for this page empties |
| 10 | Try Wikipedia, MDN, docs.python.org, react.dev, a news article, a GitHub README | Sensible answers. The header shows "~N tokens · M chunks indexed" |
| 11 | On a page whose content changes (e.g. a GitHub issue after loading more comments), click ↻ (re-read) and ask again | The answer reflects the new content |
| 12 | Switch macOS to dark mode | The panel follows the dark theme |
| 13 | Press Shift+Enter in the box | Inserts a new line. Enter sends |

### Library

| # | Do this | Expect |
|---|---|---|
| 14 | On a docs page, click ☆ | It turns into a filled ★ and "Saved to your library." appears |
| 15 | Save two or three more pages, then click ▤ | The chat is replaced by the library: saved pages, newest first, with site and date |
| 16 | Type a question in the library search box | After a moment, passages from the matching pages, each with page title, section and site |
| 17 | Click a result for a page that isn't open | It opens in a new tab and the passage is highlighted. For a page that's already open, that tab is focused instead |
| 18 | Click × on a saved page | It disappears from the list. If it's the current page, the ★ goes back to ☆ |
| 19 | Click ★ on a saved page | "Removed from your library." and the star empties |
| 20 | Restart the backend, then open ▤ | Saved pages are still there |
| 21 | Click ▤ again (or Esc in an empty search box) | Back to the chat |

### Smart Find

| # | Do this | Expect |
|---|---|---|
| 22 | On https://docs.python.org/3/tutorial/datastructures.html press **Alt+Shift+F** (or click the magnifier, or Cmd/Ctrl+F inside the panel) | The find bar opens with the cursor in it. If the panel was closed, it opens first |
| 23 | Type "remove all items from a list" | After a moment: "1 of 5", the matches listed with their section, and "Remove all items from the list." highlighted yellow on the page (it stays until you close the bar) |
| 24 | Press Enter, then Shift+Enter (or ↓ / ↑) | Moves to the next / previous match on the page; the list marks the current one |
| 25 | Click a match in the list | Jumps to that one |
| 26 | Type something the page doesn't cover, e.g. "how to bake bread" | "No match" and a hint; the old highlight is cleared |
| 27 | Search "create a branch and switch to it" on https://git-scm.com/book/en/v2/Git-Branching-Basic-Branching-and-Merging | The lead-in sentence is highlighted together with the command after it (`git checkout -b iss53`) |
| 28 | Press Esc | The bar closes and the highlight disappears |

If Alt+Shift+F does nothing, check chrome://extensions/shortcuts (another extension may already use it).

### Pages it can't read

| # | Do this | Expect |
|---|---|---|
| 14 | Open `chrome://extensions` or `chrome://settings` | "Chrome doesn't allow extensions to read this page." The input is disabled |
| 15 | Open https://chromewebstore.google.com | Same message |
| 16 | Open a new blank tab | Same message (input disabled) |
| 17 | Open a PDF (e.g. https://arxiv.org/pdf/1706.03762) and ask something | "PDFs aren't supported yet…" |
| 18 | Open a local file (`file:///…/something.html`) | Asks you to enable **Allow access to file URLs** on the PageSense card. Enable it, reload the panel, and it works |
| 19 | Open a page with almost no text (e.g. https://example.com, or an image URL) | "This page doesn't have enough readable text yet…" (example.com may just barely pass and answer from its two sentences) |
| 20 | Ask on a very long page (e.g. https://en.wikipedia.org/wiki/List_of_largest_cities, or any page over ~400k characters) | Either works, or shows "Page text exceeds 400000 characters." |

### Things breaking

| # | Do this | Expect |
|---|---|---|
| 21 | Stop the backend (Ctrl+C in the `start.sh` terminal), then ask a question | "Can't reach the PageSense backend at http://localhost:8000. Start it with: …". The dot turns red within 30 s |
| 22 | Start it again (`scripts/start.sh`) and ask | Works again. The dot goes green after the next answer or within 30 s |
| 23 | Stop Ollama (`brew services stop ollama`), wait 30 s, open ⚙ | Amber dot, "Ollama isn't reachable. Start it with: ollama serve" |
| 24 | Ask a question with Ollama stopped | "The language model isn't available. Make sure Ollama is running…" |
| 25 | `brew services start ollama` and ask again | Works |
| 26 | In ⚙ set the backend URL to `http://localhost:9999` and Save | Red dot. Questions show the "Can't reach" error. Set it back to `http://localhost:8000` |
| 27 | Restart the backend between two questions on the same page | The second question still works (the page is re-sent automatically) |
| 28 | Ask a question, then navigate the tab to another site while it's answering | The answer finishes and is saved to the original page's chat. The panel switches to the new page |
| 29 | After navigating away, scroll the panel back to an old answer and click its citation | Toast: "That answer was about a different page." |
| 30 | Press Enter repeatedly / ask while an answer is streaming | Nothing extra is sent. The send button stays disabled until the answer finishes |
| 31 | Paste a 2,000+ character question | The box stops at 2,000 characters |

### Where to look when something's wrong

- **Side panel errors**: right-click inside the panel → **Inspect** → Console.
- **Background worker**: `chrome://extensions` → PageSense → "service worker" link.
- **Backend**: the `start.sh` terminal logs every `/index` and `/ask` with timings.
- **Ollama**: `ollama ps` shows whether the model is loaded. `curl localhost:8000/health` shows the whole chain.

## 4. Re-run the evaluation (optional)

```bash
cd ~/PageSense/backend
.venv/bin/python -m eval.eval_retrieval     # retrieval accuracy
.venv/bin/python -m eval.eval_library       # same questions, searched across all pages saved in one library
.venv/bin/python -m eval.eval_find          # Smart Find: does the highlighted sentence contain the answer?
.venv/bin/python -m eval.bench_latency      # retrieval latency vs page size
.venv/bin/python -m eval.eval_answers       # 55 questions end to end (~6 min); then grade answers.csv
```
