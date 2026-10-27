// PageSense side panel: extracts the active tab's text, indexes it on the
// backend, and streams grounded answers with clickable sources. Smart Find jumps
// to the sentences that match what you mean; pages can be saved to a local
// library and searched across later.
const DEFAULT_BACKEND = "http://localhost:8000";
const INSTALL_PAGE = "https://anayabenada.github.io/PageSense/#install";
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
  saved: null, // library entry for the current tab's URL, if saved
  libraryOpen: false,
  find: null, // { query, matches, i, tabId } for the open Smart Find
  fileAccess: false, // "Allow access to file URLs" toggle in chrome://extensions
};

// ---------- utilities ----------

class UserFacingError extends Error {}

function sameUrl(a, b) {
  return a.split("#")[0] === b.split("#")[0];
}

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
  // Without the "tabs" permission Chrome hides the URL of pages extensions can't read (chrome://, the Web Store…).
  if (!url) return "PageSense can't read this tab. Open a regular web page.";
  if (RESTRICTED.test(url) || WEB_STORE.test(url)) return "Chrome doesn't allow extensions to read this page.";
  if (/^file:/i.test(url) && !state.fileAccess) return 'To read local files, enable "Allow access to file URLs" for PageSense in chrome://extensions.';
  return null;
}

async function api(path, body, { method = body ? "POST" : "GET", signal } = {}) {
  let res;
  try {
    res = await fetch(state.backend + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    const err = new UserFacingError(`Can't reach the PageSense backend at ${state.backend}. If you installed PageSense, run the install command again to restart it; in a dev setup, run scripts/start.sh.`);
    err.offline = true;
    throw err;
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

// Highlights the given snippets in the page and scrolls to the first. Unless
// persist is set, the highlight fades after 8 s.
function highlightInPage(snippets, persist = false) {
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
    if (!persist) window.__pagesenseTimer = setTimeout(() => CSS.highlights.delete("pagesense"), 8000);
  }
  const el = ranges[0].range.startContainer.parentElement;
  el?.scrollIntoView({ behavior: "smooth", block: "center" });
  return true;
}

function clearPageHighlight() {
  clearTimeout(window.__pagesenseTimer);
  window.CSS?.highlights?.delete("pagesense");
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

async function highlightSnippets(tabId, snippets, persist = false) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: highlightInPage,
    args: [snippets, persist],
  });
  return result;
}

function highlightSource(tabId, text) {
  return highlightSnippets(tabId, snippetsFor(text));
}

async function showSource(source, pageUrl) {
  const tab = await getActiveTab();
  if (!tab || !sameUrl(tab.url, pageUrl)) {
    toast("That answer was about a different page.");
    return;
  }
  try {
    if (!(await highlightSource(tab.id, source.text))) toast("Couldn't locate that passage on the page (it may have changed).");
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
  } else if (state.page && tab && sameUrl(state.page.url, tab.url)) {
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

// ---------- smart find ----------

let findRun = 0;
let findTimer;

function findSnippets(m) {
  // Each piece whole, plus its start in case inline markup splits the text oddly.
  return [m.text, m.then].filter(Boolean).flatMap((t) => (t.length > 60 ? [t, t.slice(0, 50)] : [t]));
}

function openFind() {
  if (state.libraryOpen) toggleLibrary(false);
  $("find").hidden = false;
  $("find-btn").classList.add("on");
  $("find-query").focus();
  $("find-query").select();
}

function closeFind() {
  clearTimeout(findTimer);
  findRun++;
  $("find").hidden = true;
  $("find-btn").classList.remove("on");
  resetFind();
}

function resetFind(message = "") {
  const tabId = state.find?.tabId;
  if (tabId != null) chrome.scripting.executeScript({ target: { tabId }, func: clearPageHighlight }).catch(() => {});
  state.find = null;
  $("find-count").textContent = message;
  $("find-results").replaceChildren();
}

async function runFind() {
  clearTimeout(findTimer);
  const run = ++findRun;
  const query = $("find-query").value.trim();
  if (!query) return resetFind();
  const reason = restrictedReason(state.tab?.url);
  if (reason) {
    resetFind();
    return toast(reason);
  }
  $("find-count").textContent = "…";
  try {
    // Re-read each time so matches track what's on screen; unchanged pages are cached.
    let page = await indexActivePage();
    let res;
    try {
      res = await api("/find", { page_id: page.page_id, query });
    } catch (e) {
      if (e.code !== "page_not_indexed") throw e;
      page = await indexActivePage();
      res = await api("/find", { page_id: page.page_id, query });
    }
    const { matches } = await res.json();
    if (run !== findRun) return; // a newer search started
    if (!matches.length) {
      resetFind("No match");
      const p = document.createElement("p");
      p.className = "muted small";
      p.textContent = "Nothing on this page matches that. Try other words, or ask a question below.";
      $("find-results").append(p);
      return;
    }
    state.find = { query, matches, i: 0, tabId: state.tab.id };
    await showMatch(0);
  } catch (e) {
    if (run !== findRun) return;
    resetFind();
    toast(e.message);
  }
}

function renderFind() {
  const f = state.find;
  $("find-count").textContent = `${f.i + 1} of ${f.matches.length}`;
  $("find-results").replaceChildren(
    ...f.matches.map((m, i) => {
      const b = document.createElement("button");
      b.className = `find-match${i === f.i ? " on" : ""}`;
      const section = m.heading.split(" > ").pop();
      b.innerHTML = `${section && section !== m.text ? `<div class="lib-section">${md.escape(section)}</div>` : ""}
        <div class="snippet">${md.escape(m.then ? `${m.text} ${m.then}` : m.text)}</div>`;
      b.addEventListener("click", () => showMatch(i));
      return b;
    })
  );
  $("find-results").children[f.i]?.scrollIntoView({ block: "nearest" });
}

async function showMatch(i) {
  const f = state.find;
  if (!f) return;
  f.i = (i + f.matches.length) % f.matches.length;
  renderFind();
  try {
    if (!(await highlightSnippets(f.tabId, findSnippets(f.matches[f.i]), true))) {
      toast("Couldn't locate that sentence on the page (it may have changed).");
    }
  } catch (e) {
    toast(`Couldn't highlight: ${e.message}`);
  }
}

// ---------- library ----------

function renderSaveButton() {
  const b = $("save-page");
  b.disabled = !!restrictedReason(state.tab?.url);
  b.textContent = state.saved ? "★" : "☆";
  b.title = state.saved ? "Saved to your library. Click to remove it." : "Save to library";
  b.classList.toggle("on", !!state.saved);
}

async function refreshSaved() {
  const url = state.tab?.url;
  state.saved = null;
  renderSaveButton();
  if (!url || restrictedReason(url)) return;
  try {
    const { pages } = await (await api(`/library/pages?url=${encodeURIComponent(url)}`)).json();
    if (state.tab?.url !== url) return; // tab changed while waiting
    state.saved = pages[0] || null;
    renderSaveButton();
  } catch {} // backend offline: the health dot already says so
}

async function toggleSave() {
  const b = $("save-page");
  b.disabled = true;
  try {
    if (state.saved) {
      await api(`/library/pages/${state.saved.id}`, null, { method: "DELETE" });
      state.saved = null;
      toast("Removed from your library.");
    } else {
      // Re-read so the saved copy matches what's on screen; unchanged pages are cached.
      const page = await indexActivePage();
      state.saved = await (await api("/library/pages", { page_id: page.page_id })).json();
      toast("Saved to your library.");
    }
  } catch (e) {
    toast(e.message);
  } finally {
    renderSaveButton();
    if (state.libraryOpen) renderLibrary();
  }
}

function toggleLibrary(open = !state.libraryOpen) {
  if (open && !$("find").hidden) closeFind();
  state.libraryOpen = open;
  $("library").hidden = !open;
  for (const id of ["page-info", "chat", "ask-form"]) $(id).hidden = open;
  $("library-btn").classList.toggle("on", open);
  if (open) {
    renderLibrary();
    $("library-query").focus();
  }
}

function siteOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function pageCard(p) {
  const el = document.createElement("div");
  el.className = "lib-page";
  const date = new Date(p.saved_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  el.innerHTML = `<button class="open" title="${md.escape(p.url)}"><div class="lib-title">${md.escape(p.title || p.url)}</div>
    <div class="muted small">${md.escape(siteOf(p.url))} · saved ${date}</div></button>
    <button class="icon-btn remove" title="Remove from library">×</button>`;
  el.querySelector(".open").addEventListener("click", () => openSaved(p.url));
  el.querySelector(".remove").addEventListener("click", async () => {
    try {
      await api(`/library/pages/${p.id}`, null, { method: "DELETE" });
      if (state.saved?.id === p.id) {
        state.saved = null;
        renderSaveButton();
      }
      renderLibrary();
    } catch (e) {
      toast(e.message);
    }
  });
  return el;
}

function resultCard(r) {
  const b = document.createElement("button");
  b.className = "source";
  b.title = `Open ${r.page.url}`;
  // Headings are a "Page > Section > Subsection" path; the innermost one is enough.
  const section = r.heading.split(" > ").pop();
  b.innerHTML = `<div class="head"><span>${md.escape(r.page.title || r.page.url)}</span><span>${md.escape(siteOf(r.page.url))}</span></div>
    ${section && section !== r.page.title ? `<div class="lib-section">${md.escape(section)}</div>` : ""}
    <div class="snippet">${md.escape(displaySnippet(r.text))}</div>`;
  b.addEventListener("click", () => openSaved(r.page.url, r.text));
  return b;
}

let libraryRun = 0;
async function renderLibrary() {
  const run = ++libraryRun;
  const q = $("library-query").value.trim();
  const meta = $("library-meta");
  const list = $("library-list");
  try {
    if (!q) {
      const { pages } = await (await api("/library/pages")).json();
      if (run !== libraryRun) return;
      meta.textContent = pages.length
        ? `${pages.length} saved page${pages.length > 1 ? "s" : ""}`
        : "Nothing saved yet. Click ☆ on any page to add it to your library.";
      list.replaceChildren(...pages.map(pageCard));
    } else {
      const { results, search_ms } = await (await api("/library/search", { query: q })).json();
      if (run !== libraryRun) return;
      meta.textContent = results.length
        ? `${results.length} passage${results.length > 1 ? "s" : ""} · ${(search_ms / 1000).toFixed(2)}s`
        : "No matching passages in your library.";
      list.replaceChildren(...results.map(resultCard));
    }
  } catch (e) {
    if (run !== libraryRun) return;
    meta.textContent = e.message;
    list.replaceChildren();
  }
}

function waitForLoad(tabId, timeoutMs = 15000) {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(onUpdated);
      resolve();
    };
    const onUpdated = (id, info) => id === tabId && info.status === "complete" && done();
    const timer = setTimeout(done, timeoutMs);
    chrome.tabs.onUpdated.addListener(onUpdated);
    chrome.tabs.get(tabId).then((t) => t.status === "complete" && done(), done);
  });
}

// Switches to (or opens) a saved page and, given a passage, highlights it there.
async function openSaved(url, text) {
  try {
    let tab = (await chrome.tabs.query({})).find((t) => t.url && sameUrl(t.url, url));
    if (tab) {
      await chrome.tabs.update(tab.id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
    } else {
      tab = await chrome.tabs.create({ url });
    }
    if (!text) return;
    await waitForLoad(tab.id);
    let found = await highlightSource(tab.id, text);
    if (!found) {
      // Pages that render after "complete" get a second try.
      await new Promise((r) => setTimeout(r, 1500));
      found = await highlightSource(tab.id, text);
    }
    if (!found) toast("Couldn't find that passage on the page. It may have changed since you saved it.");
  } catch (e) {
    toast(`Couldn't open that page: ${e.message}`);
  }
}

// ---------- health & settings ----------

async function checkHealth() {
  const dot = $("status-dot");
  const detail = $("health-detail");
  try {
    const h = await (await api("/health")).json();
    showSetup(false);
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
    if (e.offline) showSetup(true);
  }
}

// Shown instead of the chat while the backend can't be reached, e.g. right after
// installing the extension from the Web Store, before the backend is installed.
let setupPoll = null;
function showSetup(on) {
  if (document.body.classList.contains("needs-setup") === on) return;
  document.body.classList.toggle("needs-setup", on);
  $("setup").hidden = !on;
  clearInterval(setupPoll);
  // Check often while waiting, so the panel comes alive as soon as setup finishes.
  if (on) setupPoll = setInterval(checkHealth, 5000);
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
    if (state.find) resetFind(); // matches were for the old page; Enter searches again
    refreshSaved();
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

$("find-btn").addEventListener("click", () => ($("find").hidden ? openFind() : closeFind()));
$("find-close").addEventListener("click", closeFind);
$("find-next").addEventListener("click", () => (state.find ? showMatch(state.find.i + 1) : runFind()));
$("find-prev").addEventListener("click", () => state.find && showMatch(state.find.i - 1));
$("find-query").addEventListener("input", () => {
  clearTimeout(findTimer);
  findTimer = setTimeout(runFind, 400);
});
$("find-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    if (state.find && state.find.query === e.target.value.trim()) showMatch(state.find.i + (e.shiftKey ? -1 : 1));
    else runFind();
  } else if (e.key === "Escape") {
    e.preventDefault();
    closeFind();
  }
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
    e.preventDefault();
    openFind();
  }
});
// Alt+Shift+F (see background.js) sets this flag, whether or not the panel was open.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "session" && changes.openFind?.newValue) openFind();
});

$("save-page").addEventListener("click", toggleSave);
$("library-btn").addEventListener("click", () => toggleLibrary());
let libraryTimer;
$("library-query").addEventListener("input", () => {
  clearTimeout(libraryTimer);
  libraryTimer = setTimeout(renderLibrary, 300);
});
$("library-query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    clearTimeout(libraryTimer);
    renderLibrary();
  } else if (e.key === "Escape" && !e.target.value) {
    toggleLibrary(false);
  }
});

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
$("setup-open").addEventListener("click", () => chrome.tabs.create({ url: INSTALL_PAGE }));
$("setup-retry").addEventListener("click", async () => {
  await checkHealth();
  if (document.body.classList.contains("needs-setup")) toast("Still can't reach PageSense. If you just ran the installer, give it a minute.");
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
  try {
    const { openFind: requestedAt } = await chrome.storage.session.get("openFind");
    if (requestedAt && Date.now() - requestedAt < 5000) openFind();
  } catch {}
  checkHealth();
  setInterval(checkHealth, 30000);
})();
