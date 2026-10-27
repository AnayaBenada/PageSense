const INSTALL_PAGE = "https://anayabenada.github.io/PageSense/#install";

// Clicking the toolbar icon (or Alt+Shift+P) opens the side panel.
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);
  // First install (e.g. from the Web Store): if the backend isn't set up yet, show how.
  if (reason !== "install") return;
  try {
    await fetch("http://localhost:8000/health");
  } catch {
    chrome.tabs.create({ url: INSTALL_PAGE });
  }
});
chrome.runtime.onStartup.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);
});

// Alt+Shift+F opens the panel with Smart Find focused. sidePanel.open() must run
// straight from the shortcut, before any await; the panel watches for the flag.
chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== "smart-find" || !tab) return;
  chrome.sidePanel.open({ windowId: tab.windowId }).catch(console.error);
  chrome.storage.session.set({ openFind: Date.now() });
});
