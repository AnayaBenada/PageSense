// Minimal, safe Markdown renderer for model answers: everything is HTML-escaped
// first, then a small subset is re-introduced (code, bold, italics, lists, links,
// headings) plus [n] citation buttons.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  function inline(s, maxCite) {
    const codes = [];
    s = s.replace(/`([^`\n]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
    s = esc(s);
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // Citations like [2] or [1, 3]; only numbers that match a real source become buttons.
    s = s.replace(/\[(\d+(?:\s*,\s*\d+)*)\]/g, (m, nums) => {
      const btns = nums
        .split(",")
        .map((n) => +n.trim())
        .filter((n) => n >= 1 && n <= maxCite)
        .map((n) => `<button class="cite" data-cite="${n}" title="Show source ${n} on the page">${n}</button>`);
      return btns.length ? btns.join("") : "";
    });
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  }

  function render(md, maxCite = 0) {
    const lines = md.replace(/\r/g, "").split("\n");
    const html = [];
    let list = null; // "ul" | "ol"
    let para = [];

    const flushPara = () => {
      if (para.length) html.push(`<p>${inline(para.join("\n"), maxCite).replace(/\n/g, "<br>")}</p>`);
      para = [];
    };
    const closeList = () => {
      if (list) html.push(`</${list}>`);
      list = null;
    };

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const fence = line.match(/^\s*```(\w*)/);
      if (fence) {
        flushPara(); closeList();
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        html.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
        continue;
      }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        flushPara(); closeList();
        html.push(`<p><strong>${inline(h[2], maxCite)}</strong></p>`);
        continue;
      }
      const ul = line.match(/^\s*[-*•]\s+(.*)$/);
      const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        flushPara();
        const kind = ul ? "ul" : "ol";
        if (list !== kind) { closeList(); html.push(`<${kind}>`); list = kind; }
        html.push(`<li>${inline((ul || ol)[1], maxCite)}</li>`);
        continue;
      }
      if (!line.trim()) { flushPara(); closeList(); continue; }
      if (list && /^\s{2,}\S/.test(line)) {
        // continuation of the previous list item
        html[html.length - 1] = html[html.length - 1].replace(/<\/li>$/, ` ${inline(line.trim(), maxCite)}</li>`);
        continue;
      }
      closeList();
      para.push(line);
    }
    flushPara(); closeList();
    return html.join("");
  }

  window.PageSenseMarkdown = { render, escape: esc };
})();
