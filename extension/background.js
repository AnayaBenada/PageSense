// Clicking the toolbar icon (or Alt+Shift+P) opens the side panel.
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);
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
