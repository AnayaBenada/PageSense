# Chrome Web Store submission

Everything the Chrome Web Store developer dashboard asks for, ready to paste.
Build the upload with `scripts/package_extension.sh` (→ `dist/pagesense-<version>.zip`).

## Store listing tab

**Name:** PageSense (from the manifest)

**Summary** (from the manifest, 112/132 characters):
> Ask questions about the page you're reading and find passages by meaning. Private: the AI runs on your computer.

**Description:**

```
Ask any web page a question, and get the answer from the page itself.

PageSense opens beside the page you're reading. Ask in plain words ("Can I get a refund after two weeks?", "How do I install this?") and it answers from the page, citing the exact passages it used. Click a citation to jump to it on the page, highlighted.

FEATURES
• Answers from the page: every answer cites its sources, so you can check it.
• Smart Find: like Ctrl+F, but by meaning. Search "how do I cancel" and it highlights "end your plan". Press Alt+Shift+F.
• Reading library: save pages with ☆, then search everything you've saved by meaning and reopen any page at the passage you need.
• Keyboard shortcuts: Alt+Shift+P opens PageSense, Alt+Shift+F opens Smart Find.

PRIVATE BY DESIGN
The AI runs on your own computer (Meta's LLaMA 3.2 via Ollama, plus small search models). Pages, questions and your library never leave your machine. No account, no analytics, no ads.

ONE-TIME SETUP
Because the AI runs locally, PageSense needs a free helper app on your Mac or Linux computer. After you add the extension, it shows you how: paste one command into Terminal and wait about 10 minutes for the download. Needs about 4 GB of free space and 8 GB of memory; Apple Silicon Macs recommended.

Setup guide: https://anayabenada.github.io/PageSense/
Open source: https://github.com/AnayaBenada/PageSense
```

**Category:** Tools (alternative: Workflow & Planning)

**Language:** English

**Graphics** (all in `store/`):

| Field | File |
|---|---|
| Store icon (128×128) | `extension/icons/icon128.png` |
| Screenshots (1280×800) | `screenshot-1-ask.png`, `screenshot-2-find.png`, `screenshot-3-library.png`, `screenshot-4-setup.png` |
| Small promo tile (440×280) | `promo-small.png` |
| Marquee promo tile (1400×560, optional) | `promo-marquee.png` |

**Official URL / Homepage:** https://anayabenada.github.io/PageSense/
**Support URL:** https://github.com/AnayaBenada/PageSense/issues

## Privacy practices tab

**Single purpose:**
> PageSense helps people understand the web page they're reading: it answers questions about the page, finds passages by meaning, and lets them save and search pages they've read.

**Permission justifications:**

| Permission | Justification |
|---|---|
| `sidePanel` | PageSense's whole interface is a side panel shown next to the page the user is reading. |
| `scripting` | When the user asks a question, uses Smart Find, or saves a page, PageSense injects a script into the current tab to read its text, and another to highlight the passage an answer came from. |
| `storage` | Keeps the chat for each page (session storage, cleared when Chrome closes) and the address of the local helper app (sync storage). |
| Host permission `<all_urls>` | Users can ask about any page they're reading, so PageSense must be able to read and highlight text on any site when they use it. It is also needed to reach the helper app running on the user's own computer (http://localhost:8000). Page text is only read when the user uses a feature, and is only sent to that local app. |

**Remote code:** No, I am not using remote code. (All JavaScript is in the package; the extension only exchanges JSON with the local helper app.)

**Data usage:** check **Website content** (the extension reads the text of pages the user asks about). Leave everything else unchecked. Then certify all three:
- I do not sell or transfer user data to third parties, outside of the approved use cases
- I do not use or transfer user data for purposes that are unrelated to my item's single purpose
- I do not use or transfer user data to determine creditworthiness or for lending purposes

**Privacy policy URL:** https://anayabenada.github.io/PageSense/privacy.html

## Distribution tab

Visibility: **Public** (or **Unlisted** to share only by link at first). Regions: all.

## Note for the reviewer

Paste into "Notes for the review team" (on the submission page):

```
PageSense runs its AI locally, so it needs a companion app (open source, same repo) listening on http://localhost:8000. Without it, the side panel shows a "One-time setup" screen linking to install instructions; this is expected.

To test the full functionality on macOS or Linux:
1. Run: curl -fsSL https://raw.githubusercontent.com/AnayaBenada/PageSense/main/install.sh | bash
   (installs Ollama, a 2 GB model and the backend; about 10 minutes)
2. Open any article, e.g. https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch
3. Click the PageSense icon and ask "How do I cancel a request?"
4. Press Alt+Shift+F and search "send JSON in the body" to try Smart Find.
Remove it afterwards with ~/PageSense/uninstall.sh --all

Source code: https://github.com/AnayaBenada/PageSense
```

## After it's approved

- Update step 3 on the install page (`docs/index.html`) to link to the store listing instead of Developer mode / Load unpacked.
- For each update: bump `version` in `extension/manifest.json`, run `scripts/package_extension.sh`, and upload the new zip under **Package**.
