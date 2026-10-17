// PageSense side panel: extracts the active tab's text, indexes it on the
// backend, and streams grounded answers with clickable sources.
const DEFAULT_BACKEND = "http://localhost:8000";
const HISTORY_TURNS = 3;
const RESTRICTED = /^(chrome|chrome-extension|chrome-search|chrome-untrusted|devtools|edge|about|view-source|data|javascript):/i;
const WEB_STORE = /^https:\/\/(chrome\.google\.com\/webstore|chromewebstore\.google\.com)/i;

const $ = (id) => document.getElementById(id);
const md = window.PageSenseMarkdown;

const state = {
  backend: DEFAULT_BACKEND,
  tab: null, // { id, url, title }
  page: null, // { url, page_id, n_chunks, n_tokens }
  busy: false,
  fileAccess: false, // "Allow access to file URLs" toggle in chrome://extensions
};

// ---------- utilities ----------

class UserFacingError extends Error {}

function chatKey(url) {
  return `chat:${url.split("#")[0]}`;
}

async function loadChat(url) {
  try {
    const key = chatKey(url);
    return (await chrome.storage.session.get(key))[key] || [];
  } catch {
    return [];
  }
}

async function saveChat(url, chat) {
  try {
    await chrome.storage.session.set({ [chatKey(url)]: chat.slice(-30) });
  } catch (e) {
    console.warn("Could not save chat", e);
  }
}

function restrictedReason(url) {
  if (!url) return "No page is open in this tab.";
  if (RESTRICTED.test(url) || WEB_STORE.test(url)) return "Chrome doesn't allow extensions to read this page.";
  if (/^file:/i.test(url) && !state.fileAccess) return 'To read local files, enable "Allow access to file URLs" for PageSense in chrome://extensions.';
  return null;
}

async function api(path, body, signal) {
  let res;
  try {
    res = await fetch(state.backend + path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    throw new UserFacingError(`Can't reach the PageSense backend at ${state.backend}. Start it with:\nuvicorn app.main:app --port 8000`);
  }
  if (!res.ok) {
    let detail = {};
    try {
      detail = (await res.json()).detail || {};
    } catch {}
    const err = new UserFacingError(detail.message || `Backend error (${res.status})`);
    err.code = detail.code;
    err.status = res.status;
    throw err;
  }
  return res;
}

// ---------- page access ----------

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab ? { id: tab.id, url: tab.url || tab.pendingUrl || "", title: tab.title || "" } : null;
}

async function extractPage(tabId) {
  let results;
  try {
    results = await chrome.scripting.executeScript({ target: { tabId }, files: ["extractor.js"] });
  } catch (e) {
    const url = state.tab?.url || "";
    if (/\.pdf($|\?)/i.test(url)) throw new UserFacingError("PDFs aren't supported yet. Open an HTML page instead.");
    throw new UserFacingError(`Couldn't read this page: ${e.message}`);
  }
  const page = results?.[0]?.result;
  if (!page || page.text.replace(/^# .*$/m, "").trim().length < 40) {
    throw new UserFacingError("This page doesn't have enough readable text yet. If it's still loading, try again in a moment.");
  }
  return page;
}

async function indexActivePage() {
  const tab = state.tab;
  const reason = restrictedReason(tab?.url);
  if (reason) throw new UserFacingError(reason);
  const page = await extractPage(tab.id);
  const res = await api("/index", { url: page.url, title: page.title, text: page.text });
  const info = await res.json();
  state.page = { url: page.url, ...info };
  renderPageInfo();
  return state.page;
}

// Highlights the best-matching text of a source chunk in the page and scrolls to it.
function highlightInPage(snippets) {
  const STYLE_ID = "pagesense-highlight-style";
  if (!document.getElementById(STYLE_ID)) {
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = "::highlight(pagesense){background:#fde047;color:#111;}";
    document.head.appendChild(style);
  }

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) =>
      n.parentElement && !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.parentElement.tagName)
        ? NodeFilter.FILTER_ACCEPT
        : NodeFilter.FILTER_REJECT,
  });
  // Build a whitespace-normalized, lowercased copy of the page text with a map back to (node, offset).
  const nodes = [];
  const offsets = [];
  let norm = "";
  let lastSpace = true;
  for (let n; (n = walker.nextNode()); ) {
    const v = n.nodeValue;
    for (let i = 0; i < v.length; i++) {
      const isSpace = /\s/.test(v[i]);
      if (isSpace && lastSpace) continue;
      norm += isSpace ? " " : v[i].toLowerCase();
      nodes.push(n);
      offsets.push(i);
      lastSpace = isSpace;
    }
  }

  const ranges = [];
  for (const snippet of snippets) {
    const needle = snippet.replace(/\s+/g, " ").trim().toLowerCase();
    if (needle.length < 8) continue;
    const at = norm.indexOf(needle);
    if (at === -1) continue;
    const end = at + needle.length - 1;
    const range = document.createRange();
    range.setStart(nodes[at], offsets[at]);
    range.setEnd(nodes[end], offsets[end] + 1);
    ranges.push({ at, range });
  }
  if (!ranges.length) return false;
  ranges.sort((a, b) => a.at - b.at);

  if (window.CSS && CSS.highlights) {
    CSS.highlights.set("pagesense", new Highlight(...ranges.map((r) => r.range)));
    clearTimeout(window.__pagesenseTimer);
    window.__pagesenseTimer = setTimeout(() => CSS.highlights.delete("pagesense"), 8000);
  }
  const el = ranges[0].range.startContainer.parentElement;
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
  return true;
}

function snippetsFor(chunkText) {
  // Strip the Markdown-ish markers our extractor added; keep the longest lines.
  const lines = chunkText
    .split("\n")
    .map((l) =>
      l
        .replace(/^#{1,6}\s+/, "")
        .replace(/^-\s+/, "")
        .replace(/^```.*$/, "")
        .replace(/\[image: [^\]]*\]/g, "")
        .replace(/\s*\|\s*/g, " ")
        .trim()
    )
    .filter((l) => l.length >= 12);
  lines.sort((a, b) => b.length - a.length);
  const out = [];
  for (const l of lines.slice(0, 4)) {
    out.push(l.slice(0, 160));
    if (l.length > 60) out.push(l.slice(0, 50)); // shorter fallback if inline markup splits the text oddly
  }
  return out;
}

async function showSource(source, pageUrl) {
  const tab = await getActiveTab();
  if (!tab || tab.url.split("#")[0] !== pageUrl.split("#")[0]) {
    toast("That answer was about a different page.");
    return;
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: highlightInPage,
      args: [snippetsFor(source.text)],
    });
    if (!result) toast("Couldn't locate that passage on the page (it may have changed).");
  } catch (e) {
    toast(`Couldn't highlight: ${e.message}`);
  }
}

// ---------- rendering ----------

function renderPageInfo() {
  const tab = state.tab;
  $("page-title").textContent = tab?.title || tab?.url || "—";
  $("page-title").title = tab?.url || "";
  const reason = restrictedReason(tab?.url);
  const meta = $("page-meta");
  if (reason) {
    meta.textContent = reason;
  } else if (state.page && tab && state.page.url.split("#")[0] === tab.url.split("#")[0]) {
    meta.textContent = `~${state.page.n_tokens.toLocaleString()} tokens · ${state.page.n_chunks} chunks indexed`;
  } else {
    meta.textContent = "Not read yet. It'll be read when you ask a question.";
  }
  $("question").disabled = !!reason;
  $("send").disabled = !!reason || state.busy;
}

function scrollChat() {
  const chat = $("chat");
  chat.scrollTop = chat.scrollHeight;
}

function addUserMessage(text) {
  $("empty").hidden = true;
  const el = document.createElement("div");
  el.className = "msg user";
  el.textContent = text;
  $("chat").appendChild(el);
  scrollChat();
}

function addBotMessage() {
  $("empty").hidden = true;
  const el = document.createElement("div");
  el.className = "msg bot";
  el.innerHTML = `<div class="content"></div>`;
  $("chat").appendChild(el);
  scrollChat();
  return el;
}

function setStatus(el, text) {
  el.querySelector(".content").innerHTML = `<div class="status"><span class="spinner"></span>${md.escape(text)}</div>`;
}

function displaySnippet(text) {
  return text
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^```.*$/gm, "")
    .replace(/\[image: [^\]]*\]\s*/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function renderAnswer(el, turn, { streaming = false } = {}) {
  const content = el.querySelector(".content");
  content.innerHTML = md.render(turn.answer, turn.sources.length) || "";
  if (streaming) content.classList.add("cursor");
  else content.classList.remove("cursor");

  content.querySelectorAll(".cite").forEach((b) =>
    b.addEventListener("click", () => showSource(turn.sources[+b.dataset.cite - 1], turn.url))
  );

  el.querySelector("details.sources")?.remove();
  el.querySelector(".timing")?.remove();
  if (streaming || !turn.sources.length) return;

  const details = document.createElement("details");
  details.className = "sources";
  details.innerHTML = `<summary>${turn.sources.length} source${turn.sources.length > 1 ? "s" : ""} from this page</summary>`;
  turn.sources.forEach((s, i) => {
    const b = document.createElement("button");
    b.className = "source";
    b.title = "Show on page";
    b.innerHTML = `<div class="head"><span>[${i + 1}] ${md.escape(s.heading || "Excerpt")}</span><span>${s.score == null ? "" : `${(s.score * 100).toFixed(0)}%`}</span></div>
      <div class="snippet">${md.escape(displaySnippet(s.text))}</div>`;
    b.addEventListener("click", () => showSource(s, turn.url));
    details.appendChild(b);
  });
  el.appendChild(details);

  if (turn.timings) {
    const t = document.createElement("div");
    t.className = "timing";
    const secs = (ms) => (ms / 1000).toFixed(ms < 1000 ? 2 : 1);
    t.textContent = `retrieval ${secs(turn.timings.retrieve_ms)}s · answer ${secs(turn.timings.generate_ms)}s`;
    el.appendChild(t);
  }
}

function renderError(el, message) {
  el.classList.add("error");
  el.querySelector(".content").textContent = message;
  scrollChat();
}

async function renderChat() {
  const chat = $("chat");
  chat.querySelectorAll(".msg").forEach((m) => m.remove());
  const turns = state.tab?.url ? await loadChat(state.tab.url) : [];
  $("empty").hidden = turns.length > 0;
  for (const turn of turns) {
    addUserMessage(turn.question);
    renderAnswer(addBotMessage(), turn);
  }
  scrollChat();
}

let toastTimer;
function toast(text) {
  let t = document.querySelector(".toast");
  if (!t) {
    t = document.createElement("div");
    t.className = "toast";
    Object.assign(t.style, {
      position: "fixed", left: "12px", right: "12px", bottom: "70px", padding: "8px 12px",
      background: "var(--fg)", color: "var(--bg)", borderRadius: "8px", fontSize: "12px", zIndex: 10,
    });
    document.body.appendChild(t);
  }
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

// ---------- asking ----------

async function* readSSE(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

async function ask(question) {
  question = question.trim();
  if (!question || state.busy || !state.tab) return;
  state.busy = true;
  renderPageInfo();

  const url = state.tab.url;
  const chat = await loadChat(url);
  addUserMessage(question);
  const el = addBotMessage();
  const turn = { question, answer: "", sources: [], timings: null, url };

  try {
    setStatus(el, "Reading the page…");
    let page = await indexActivePage();
    turn.url = page.url;
    setStatus(el, "Searching the page…");

    const history = chat.slice(-HISTORY_TURNS).map(({ question, answer }) => ({ question, answer }));
    const body = () => ({ page_id: page.page_id, question, history });
    let res;
    try {
      res = await api("/ask/stream", body());
    } catch (e) {
      if (e.code !== "page_not_indexed") throw e;
      page = await indexActivePage(); // evicted from the backend cache; re-send it
      res = await api("/ask/stream", body());
    }

    let frame = 0;
    for await (const { event, data } of readSSE(res)) {
      if (event === "meta") {
        turn.sources = data.sources;
        setStatus(el, "Writing the answer…");
      } else if (event === "token") {
        turn.answer += data.t;
        if (!frame) {
          frame = requestAnimationFrame(() => {
            frame = 0;
            renderAnswer(el, turn, { streaming: true });
            scrollChat();
          });
        }
      } else if (event === "done") {
        turn.timings = data.timings;
      } else if (event === "error") {
        throw new UserFacingError(
          data.code === "llm_unavailable"
            ? `The language model isn't available. Make sure Ollama is running (ollama serve) and the model is pulled.\n\n${data.message}`
            : data.message
        );
      }
    }
    cancelAnimationFrame(frame);
    if (!turn.answer.trim()) throw new UserFacingError("The model returned an empty answer. Try rephrasing.");
    turn.answer = turn.answer.trim();
    renderAnswer(el, turn);
    chat.push(turn);
    await saveChat(url, chat);
    if (!$("status-dot").classList.contains("ok")) checkHealth(); // it evidently works now
  } catch (e) {
    console.error(e);
    renderError(el, e instanceof UserFacingError ? e.message : `Something went wrong: ${e.message}`);
  } finally {
    state.busy = false;
    renderPageInfo();
    scrollChat();
  }
}

// ---------- health & settings ----------

async function checkHealth() {
  const dot = $("status-dot");
  const detail = $("health-detail");
  try {
    const h = await (await api("/health")).json();
    if (h.status === "ok") {
      dot.className = "dot ok";
      dot.title = `Ready · ${h.llm_model}`;
      detail.textContent = `Connected · LLM ${h.llm_model} · embeddings ${h.embed_model}`;
    } else {
      dot.className = "dot warn";
      dot.title = "Backend up, but the LLM isn't ready";
      detail.textContent = h.ollama.reachable
        ? `Ollama is running but "${h.llm_model}" isn't pulled. Run: ollama pull ${h.llm_model}`
        : "Ollama isn't reachable. Start it with: ollama serve";
    }
  } catch (e) {
    dot.className = "dot down";
    dot.title = "Backend offline";
    detail.textContent = e.message;
  }
}

async function loadSettings() {
  try {
    const { backend } = await chrome.storage.sync.get("backend");
    state.backend = (backend || DEFAULT_BACKEND).replace(/\/+$/, "");
  } catch {}
  $("backend-url").value = state.backend;
}

// ---------- tab tracking ----------

async function syncTab() {
  const tab = await getActiveTab();
  const changed = tab?.url !== state.tab?.url;
  state.tab = tab;
  if (changed) {
    state.page = null;
    await renderChat();
  }
  renderPageInfo();
}

chrome.tabs.onActivated.addListener(syncTab);
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (tabId === state.tab?.id && (info.url || info.title || info.status === "complete")) syncTab();
});
chrome.windows.onFocusChanged.addListener(syncTab);

// ---------- wiring ----------

const textarea = $("question");
textarea.addEventListener("input", () => {
  textarea.style.height = "auto";
  textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
});
textarea.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    $("ask-form").requestSubmit();
  }
});
$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = textarea.value;
  if (!q.trim() || state.busy) return;
  textarea.value = "";
  textarea.style.height = "auto";
  ask(q);
});
document.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));

$("reread").addEventListener("click", async () => {
  if (state.busy) return;
  try {
    await indexActivePage();
    toast("Page re-read.");
  } catch (e) {
    toast(e.message);
  }
});
$("clear").addEventListener("click", async () => {
  if (!state.tab?.url || state.busy) return;
  await saveChat(state.tab.url, []);
  await renderChat();
});
$("settings-btn").addEventListener("click", () => {
  $("settings").hidden = !$("settings").hidden;
  if (!$("settings").hidden) checkHealth();
});
$("save-settings").addEventListener("click", async () => {
  const value = $("backend-url").value.trim().replace(/\/+$/, "") || DEFAULT_BACKEND;
  state.backend = value;
  try {
    await chrome.storage.sync.set({ backend: value });
  } catch {}
  checkHealth();
  toast("Saved.");
});

(async function init() {
  await loadSettings();
  try {
    state.fileAccess = await chrome.extension.isAllowedFileSchemeAccess();
  } catch {}
  await syncTab();
  checkHealth();
  setInterval(checkHealth, 30000);
})();
