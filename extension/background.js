// Opens the side panel when the toolbar icon is clicked (MV3 requires opting into this
// explicitly - it's not the default action for a browser-action click).
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
