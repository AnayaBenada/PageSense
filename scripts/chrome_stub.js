// Minimal chrome.* stub so extension/sidepanel.js runs in a normal tab (see devserver.py).
// The "active tab" is a saved snapshot from backend/eval/pages/<HARNESS_PAGE>.json.
(function () {
  const xhr = new XMLHttpRequest();
  xhr.open("GET", `/backend/eval/pages/${window.HARNESS_PAGE}.json`, false); // sync: must exist before sidepanel.js runs
  xhr.send();
  const page = xhr.status === 200 ? JSON.parse(xhr.responseText) : { url: "https://example.com/", title: "No snapshot", text: "" };

  const mem = {};
  const area = {
    get: async (key) => (typeof key === "string" ? (key in mem ? { [key]: mem[key] } : {}) : { ...mem }),
    set: async (obj) => Object.assign(mem, obj),
  };
  const noopEvent = { addListener() {} };

  window.__harnessCalls = [];
  window.chrome = {
    tabs: {
      query: async () => [{ id: 1, url: page.url, title: page.title }],
      onActivated: noopEvent,
      onUpdated: noopEvent,
    },
    windows: { onFocusChanged: noopEvent },
    extension: { isAllowedFileSchemeAccess: async () => false },
    storage: { session: area, sync: area },
    scripting: {
      executeScript: async ({ files, func, args }) => {
        window.__harnessCalls.push({ files, func: func?.name, args });
        if (files) return [{ result: page }];
        return [{ result: true }]; // highlightInPage: pretend the passage was found
      },
    },
  };
})();
