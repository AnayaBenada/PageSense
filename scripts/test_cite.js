// Tests for extension/cite.js. Run: node scripts/test_cite.js
const assert = require("node:assert/strict");
const { cite, cleanTitle } = require("../extension/cite.js");

const accessed = new Date(2026, 9, 27);
const check = (meta, mla, apa, missing = []) => {
  const r = cite(meta, accessed);
  assert.equal(r.mla.text, mla);
  assert.equal(r.apa.text, apa);
  assert.deepEqual(r.missing, missing);
};

// One author, full date.
check(
  { url: "https://www.nytimes.com/2024/03/05/science/bees.html", title: "Why Bees Are Disappearing", authors: ["Jane Q. Smith"], published: "2024-03-05T10:00:00Z", siteName: "The New York Times" },
  "Smith, Jane Q. “Why Bees Are Disappearing.” The New York Times, 5 Mar. 2024, www.nytimes.com/2024/03/05/science/bees.html. Accessed 27 Oct. 2026.",
  "Smith, J. Q. (2024, March 5). Why Bees Are Disappearing. The New York Times. https://www.nytimes.com/2024/03/05/science/bees.html",
);
// No author: the title leads; only the modified date is known.
check(
  { url: "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch", title: "Using the Fetch API", authors: [], modified: "2025-07-24", siteName: "MDN Web Docs" },
  "“Using the Fetch API.” MDN Web Docs, 24 July 2025, developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch. Accessed 27 Oct. 2026.",
  "Using the Fetch API. (2025, July 24). MDN Web Docs. https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch",
  ["author"],
);
// Three authors (MLA: et al.), year only, no site name, a profile URL in the author list.
check(
  { url: "https://example.edu/paper", title: "Deep Learning for Cats", authors: ["Smith, John", "https://x.com/someone", "Ana Lopez-Garcia", "Bo Chen"], published: "2023" },
  "Smith, John, et al. “Deep Learning for Cats.” example.edu, 2023, example.edu/paper. Accessed 27 Oct. 2026.",
  "Smith, J., Lopez-Garcia, A., & Chen, B. (2023). Deep Learning for Cats. example.edu. https://example.edu/paper",
);
// Organization author that is also the site: the site isn't repeated. No date: (n.d.).
check(
  { url: "https://www.who.int/news-room/fact-sheets/detail/malaria", title: "Malaria", authors: ["World Health Organization"], siteName: "World Health Organization" },
  "World Health Organization. “Malaria.” www.who.int/news-room/fact-sheets/detail/malaria. Accessed 27 Oct. 2026.",
  "World Health Organization. (n.d.). Malaria. https://www.who.int/news-room/fact-sheets/detail/malaria",
  ["publication date"],
);
// Italics in the HTML version.
assert.match(cite({ url: "https://a.com/x", title: "T", authors: ["Al Bo"], siteName: "Site" }, accessed).mla.html, /<i>Site<\/i>/);
assert.equal(cleanTitle("Using the Fetch API - Web APIs | MDN", "MDN Web Docs"), "Using the Fetch API - Web APIs");
console.log("cite.js: all tests passed");
