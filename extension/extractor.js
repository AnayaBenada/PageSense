// Injected into the page with chrome.scripting.executeScript. The value of the
// final expression is returned to the side panel: { url, title, text }.
//
// Produces readable text with Markdown-style structure (# headings, - list items,
// ``` code blocks) so the backend chunker can keep sections together.
(() => {
  const SKIP = new Set([
    "SCRIPT", "STYLE", "NOSCRIPT", "SVG", "CANVAS", "IFRAME", "TEMPLATE", "NAV",
    "FOOTER", "ASIDE", "BUTTON", "SELECT", "INPUT", "TEXTAREA", "FORM", "DIALOG", "OBJECT", "VIDEO", "AUDIO",
  ]);
  const SKIP_ROLES = new Set(["navigation", "banner", "contentinfo", "search", "complementary", "menu", "menubar", "toolbar", "dialog", "alert"]);
  const NOISE = /(^|[\s_-])(cookie|consent|gdpr|newsletter|subscribe|advert|sponsor|social-share|share-buttons|breadcrumb|skip-link|sidebar|toc|table-of-contents|comments?|editsection|mw-editsection)([\s_-]|$)/i;
  const BLOCK = new Set([
    "P", "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "UL", "OL", "DL", "DT", "DD", "TABLE", "THEAD", "TBODY",
    "BLOCKQUOTE", "FIGURE", "FIGCAPTION", "DETAILS", "SUMMARY", "HR", "ADDRESS",
  ]);

  const out = [];
  const push = (s) => out.push(s);
  // Code blocks are held aside as placeholders so whitespace cleanup can't touch them.
  const codeBlocks = [];
  const cleanHeading = (t) => t.replace(/\s+/g, " ").replace(/\s*[¶#🔗§]\s*$/u, "").trim();

  function skip(el) {
    if (SKIP.has(el.tagName)) return true;
    const role = el.getAttribute("role");
    if (role && SKIP_ROLES.has(role)) return true;
    if (el.getAttribute("aria-hidden") === "true" || el.hidden) return true;
    const cls = typeof el.className === "string" ? el.className : "";
    if ((cls && NOISE.test(cls)) || (el.id && NOISE.test(el.id))) return true;
    if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) {
      // display:contents elements have no box of their own, so checkVisibility()
      // reports them invisible even though their children render (e.g. MDN's <main>).
      return getComputedStyle(el).display !== "contents";
    }
    return false;
  }

  function walk(node, inTable) {
    if (node.nodeType === Node.TEXT_NODE) {
      // Whitespace-only nodes still separate words (e.g. "the path <code>/</code> with").
      push(node.nodeValue.replace(/\s+/g, " "));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node;
    if (skip(el)) return;
    const tag = el.tagName;

    if (/^H[1-6]$/.test(tag)) {
      const text = cleanHeading(el.innerText);
      if (text) push(`\n\n${"#".repeat(+tag[1])} ${text}\n\n`);
      return;
    }
    if (tag === "PRE") {
      // textContent keeps the exact source (indentation, no injected blank lines).
      const code = (el.querySelector("code") || el).textContent
        .replace(/<\/?(font|span)\b[^>]*>/gi, "") // markup leaked by terminal-animation widgets
        .replace(/\n{3,}/g, "\n\n")
        .replace(/^\n+|\s+$/g, "");
      if (code.trim()) push(`\n\n\u0000${codeBlocks.push("```\n" + code + "\n```") - 1}\u0000\n\n`);
      return;
    }
    if (tag === "BR") return push("\n");
    if (tag === "IMG") {
      const alt = (el.getAttribute("alt") || "").trim();
      if (alt.length > 3) push(` [image: ${alt}] `);
      return;
    }
    if (tag === "LI") push("\n- ");
    else if (tag === "TR") push("\n");
    else if (tag === "TD" || tag === "TH") push(" | ");
    else if (BLOCK.has(tag) && !inTable) push("\n\n");

    const table = inTable || tag === "TABLE";
    if (el.shadowRoot) for (const c of el.shadowRoot.childNodes) walk(c, table);
    for (const c of el.childNodes) walk(c, table);

    if (tag === "TABLE" || (BLOCK.has(tag) && !inTable)) push("\n\n");
  }

  function render(root) {
    out.length = 0;
    codeBlocks.length = 0;
    walk(root, false);
    return out
      .join("")
      .replace(/ {2,}/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/^-\s*$/gm, "")
      .replace(/\n{3,}/g, "\n\n")
      .replace(/\u0000(\d+)\u0000/g, (_, i) => codeBlocks[+i])
      .trim();
  }

  // Prefer the main content region; fall back to <body> if it's suspiciously small.
  const candidates = [
    document.querySelector("main"),
    document.querySelector("[role=main]"),
    ...[...document.querySelectorAll("article")].sort((a, b) => b.innerText.length - a.innerText.length).slice(0, 1),
  ].filter(Boolean);

  let text = "";
  for (const c of candidates) {
    text = render(c);
    if (text.length > 500) break;
  }
  if (text.length <= 500) {
    const bodyText = render(document.body);
    if (bodyText.length > text.length) text = bodyText;
  }

  const title = document.title || document.querySelector("h1")?.innerText || "";
  if (title && !text.startsWith("# ")) text = `# ${title.trim()}\n\n${text}`;

  return { url: location.href, title: title.trim(), text, meta: citationMeta() };

  // Details for student-mode citations, from the tags publishers add for Google
  // Scholar, social sharing and search engines.
  function citationMeta() {
    const metas = (sel) => [...document.querySelectorAll(sel)].map((m) => (m.getAttribute("content") || "").trim()).filter(Boolean);
    const first = (...sels) => sels.map((s) => metas(s)[0]).find(Boolean) || "";
    let ld = [];
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(s.textContent);
        ld.push(...[data, ...(data["@graph"] || [])].flat());
      } catch {}
    }
    ld = ld.filter((d) => d && typeof d === "object");
    const article = ld.find((d) => /Article|BlogPosting|Report|ScholarlyArticle|WebPage/.test([].concat(d["@type"]).join(" "))) || {};
    const ldAuthors = [].concat(article.author || []).map((a) => (typeof a === "string" ? a : a?.name)).filter(Boolean);
    const authors = [
      ...metas('meta[name="citation_author"]'),
      ...metas('meta[name="dc.creator" i], meta[name="DC.creator"]'),
      ...ldAuthors,
      ...metas('meta[name="author"]'),
      ...[...document.querySelectorAll('[rel="author"]')].map((a) => a.textContent.trim()).filter((t) => t && t.length < 60),
    ];
    // "Using the Fetch API - Web APIs | MDN": the <h1> is the real title when the page title starts with it.
    function citationTitle() {
      const h1 = document.querySelector("main h1, article h1, h1")?.innerText.replace(/\s+/g, " ").trim() || "";
      const t = first('meta[name="citation_title"]') || article.headline || first('meta[property="og:title"]') || document.title;
      return h1 && t.toLowerCase().startsWith(h1.toLowerCase()) ? h1 : t || h1;
    }
    return {
      url: location.href,
      title: citationTitle(),
      authors,
      published: first('meta[name="citation_publication_date"]', 'meta[name="citation_date"]', 'meta[property="article:published_time"]', 'meta[name="dc.date" i]', 'meta[name="date"]')
        || article.datePublished || document.querySelector("article time[datetime], main time[datetime]")?.getAttribute("datetime") || "",
      modified: first('meta[property="article:modified_time"]') || article.dateModified || "",
      siteName: first('meta[property="og:site_name"]', 'meta[name="citation_journal_title"]', 'meta[name="application-name"]') || article.publisher?.name || "",
    };
  }
})();
