// Student mode: MLA 9 and APA 7 citations for a web page, from the metadata the
// extractor finds (citation_* / article:* / JSON-LD tags). Pure functions, so they
// can be tested in Node: each style returns { text, html } (html has the italics).
(function () {
  const MLA_MONTHS = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."];
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const ORG = /\b(contributors|team|staff|inc\.?|llc|ltd|foundation|university|institute|association|organization|society|press|editors|wikipedia|news|times|post|journal|magazine|company|corp\.?|group|department|agency|council|library|museum|community)\b/i;

  // "2024-03-05T10:00:00Z", "2024-03-05", "March 5, 2024" → { y, m, d } (m/d may be null).
  function parseDate(s) {
    if (!s) return null;
    const iso = String(s).match(/^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?/);
    if (iso) return { y: +iso[1], m: iso[2] ? +iso[2] : null, d: iso[3] ? +iso[3] : null };
    const t = Date.parse(s);
    if (Number.isNaN(t)) return null;
    const dt = new Date(t);
    return { y: dt.getFullYear(), m: dt.getMonth() + 1, d: dt.getDate() };
  }

  function isOrg(name) {
    return ORG.test(name) || name.split(/\s+/).length > 4 || !/\s/.test(name.trim());
  }

  // "Jane Q. Smith" → { last: "Smith", first: "Jane Q." }; "Smith, Jane" works too.
  function splitName(name) {
    name = name.trim().replace(/\s+/g, " ");
    if (name.includes(",")) {
      const [last, first] = name.split(",").map((s) => s.trim());
      return { last, first };
    }
    const parts = name.split(" ");
    const suffixes = /^(jr\.?|sr\.?|ii|iii|iv)$/i;
    let suffix = "";
    if (parts.length > 2 && suffixes.test(parts.at(-1))) suffix = parts.pop();
    const last = parts.pop();
    return { last: suffix ? `${last} ${suffix}` : last, first: parts.join(" ") };
  }

  const initials = (first) =>
    first.split(/[\s]+/).filter(Boolean).map((p) => p.split("-").map((q) => `${q[0].toUpperCase()}.`).join("-")).join(" ");

  function cleanAuthors(authors) {
    const seen = new Set();
    return (authors || [])
      .map((a) => String(a).replace(/^by\s+/i, "").trim())
      .filter((a) => a && !/^https?:/i.test(a) && a.length < 80)
      .filter((a) => !seen.has(a.toLowerCase()) && seen.add(a.toLowerCase()));
  }

  // The page title without a trailing " | Site" / " - Site" part.
  function cleanTitle(title, site) {
    let t = (title || "").trim();
    const parts = t.split(/\s+[|\-–—·:]\s+/);
    if (parts.length > 1) {
      const last = parts.at(-1).toLowerCase();
      if (site && (last.includes(site.toLowerCase()) || site.toLowerCase().includes(last))) parts.pop();
      else if (parts.length > 2) parts.splice(-1);
      t = parts.join(" - ");
    }
    return t.replace(/\s+/g, " ").trim();
  }

  function siteFromUrl(url) {
    try {
      const host = new URL(url).hostname.replace(/^www\./, "");
      return host;
    } catch {
      return "";
    }
  }

  const endWith = (s, ch) => (/[.!?]$/.test(s) ? s : s + ch);

  function mlaAuthors(authors) {
    const name = (a, first) => {
      if (isOrg(a)) return a;
      const { last, first: f } = splitName(a);
      return first ? (f ? `${last}, ${f}` : last) : (f ? `${f} ${last}` : last);
    };
    if (!authors.length) return "";
    if (authors.length === 1) return name(authors[0], true);
    if (authors.length === 2) return `${name(authors[0], true)}, and ${name(authors[1], false)}`;
    return `${name(authors[0], true)}, et al`;
  }

  function apaAuthors(authors) {
    const name = (a) => {
      if (isOrg(a)) return a;
      const { last, first } = splitName(a);
      return first ? `${last}, ${initials(first)}` : last;
    };
    const names = authors.slice(0, 20).map(name);
    if (names.length <= 1) return names[0] || "";
    return `${names.slice(0, -1).join(", ")}, & ${names.at(-1)}`;
  }

  function mla(m, accessed) {
    const authors = cleanAuthors(m.authors);
    const site = m.siteName || siteFromUrl(m.url);
    const title = cleanTitle(m.title, site);
    const date = parseDate(m.published || m.modified);
    const url = m.url.replace(/^https?:\/\//, "").replace(/\/$/, "");
    const who = mlaAuthors(authors);
    const dateStr = date ? [date.d, date.m && MLA_MONTHS[date.m - 1], date.y].filter(Boolean).join(" ") : "";
    const acc = `${accessed.getDate()} ${MLA_MONTHS[accessed.getMonth()]} ${accessed.getFullYear()}`;
    // The site is left out when it's also the author.
    const showSite = site && !(authors.length === 1 && authors[0].toLowerCase() === site.toLowerCase());
    const tail = [showSite ? "\u0001" : "", dateStr, url].filter(Boolean).join(", ");
    const plain = `${who ? endWith(who, ".") + " " : ""}“${endWith(title, ".")}” ${tail}. Accessed ${acc}.`;
    return {
      text: plain.replace("\u0001", site),
      html: esc(plain).replace("\u0001", `<i>${esc(site)}</i>`),
    };
  }

  function apa(m) {
    const authors = cleanAuthors(m.authors);
    const site = m.siteName || siteFromUrl(m.url);
    const title = cleanTitle(m.title, site);
    const date = parseDate(m.published || m.modified);
    const when = date ? `(${[date.y, date.m && [MONTHS[date.m - 1], date.d].filter(Boolean).join(" ")].filter(Boolean).join(", ")})` : "(n.d.)";
    const who = apaAuthors(authors);
    const showSite = site && !(authors.length === 1 && authors[0].toLowerCase() === site.toLowerCase());
    const siteText = showSite ? ` ${endWith(site, ".")}` : "";
    // With no author, the title moves to the front.
    if (who) {
      const plain = `${endWith(who, ".")} ${when}. \u0001${siteText} ${m.url}`;
      const t = endWith(title, ".");
      return { text: plain.replace("\u0001", t), html: esc(plain).replace("\u0001", `<i>${esc(t)}</i>`) };
    }
    const t = endWith(title, ".");
    const plain = `\u0001 ${when}.${siteText} ${m.url}`;
    return { text: plain.replace("\u0001", t), html: esc(plain).replace("\u0001", `<i>${esc(t)}</i>`) };
  }

  /** Both citations, plus the details that couldn't be found (so the student can check them). */
  function cite(meta, accessed = new Date()) {
    const missing = [];
    if (!cleanAuthors(meta.authors).length) missing.push("author");
    if (!parseDate(meta.published || meta.modified)) missing.push("publication date");
    return { mla: mla(meta, accessed), apa: apa(meta), missing };
  }

  const api = { cite, cleanTitle, parseDate, splitName };
  if (typeof module !== "undefined") module.exports = api;
  else window.PageSenseCite = api;
})();
