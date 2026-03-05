/**
 * CCCC Desktop - Preload Script
 *
 * This script runs in the webview context before the page loads.
 * It provides a bridge between the webview and the main process.
 */

// Expose a minimal API to the webview
const ccccDesktop = {
  platform: process.platform,
  version: "0.4.2",

  // Get app info
  getAppInfo: () => ({
    name: "CCCC",
    version: "0.4.2",
    platform: process.platform,
  }),

  // Log to main process
  log: (message: string) => {
    console.log(`[WebView] ${message}`);
  },
};

// Expose to window
(window as any).ccccDesktop = ccccDesktop;

// Log when preload is ready
console.log("[Preload] CCCC Desktop preload script loaded");