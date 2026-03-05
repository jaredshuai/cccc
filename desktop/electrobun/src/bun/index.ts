/**
 * CCCC Desktop - Main Process (Electrobun/Bun)
 *
 * This is the main entry point for the Electrobun desktop application.
 * It manages the Python backend process and the webview window.
 */

import { BrowserWindow, Screen } from "electrobun/bun";
import { spawn, Subprocess } from "bun";
import { dlopen, FFIType } from "bun:ffi";
import { dirname, join } from "node:path";

// Configuration
const CONFIG = {
  backendPort: 8848,
  backendHost: "localhost",
  startupTimeout: 30000, // 30 seconds
  retryInterval: 500,    // 500ms
  initialWindowAreaRatio: 1 / 3,
  initialWindowAspectRatio: 16 / 10,
  minWindowWidth: 900,
  minWindowHeight: 720,
};

// Global state
let mainWindow: BrowserWindow | null = null;
let backendProcess: Subprocess | null = null;
let resizeSyncTimer: ReturnType<typeof setTimeout> | null = null;
let resizePulseTimer: ReturnType<typeof setTimeout> | null = null;

const VIEWPORT_SYNC_SCRIPT = [
  "window.dispatchEvent(new Event('resize'));",
  "window.dispatchEvent(new Event('orientationchange'));",
  "window.dispatchEvent(new Event('pageshow'));",
].join("");

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function getInitialWindowFrame(): { x: number; y: number; width: number; height: number } {
  const displays = Screen.getAllDisplays();
  const cursor = Screen.getCursorScreenPoint();

  let display =
    displays.find((d) => (
      cursor.x >= d.workArea.x
      && cursor.x < d.workArea.x + d.workArea.width
      && cursor.y >= d.workArea.y
      && cursor.y < d.workArea.y + d.workArea.height
    ))
    || displays.find((d) => d.isPrimary)
    || Screen.getPrimaryDisplay();

  const area = (display.workArea.width > 0 && display.workArea.height > 0)
    ? display.workArea
    : display.bounds;

  const targetArea = Math.max(1, area.width * area.height * CONFIG.initialWindowAreaRatio);
  const targetAspect = CONFIG.initialWindowAspectRatio;
  let width = Math.round(Math.sqrt(targetArea * targetAspect));
  let height = Math.round(width / targetAspect);

  width = clamp(width, Math.min(CONFIG.minWindowWidth, area.width), area.width);
  height = clamp(height, Math.min(CONFIG.minWindowHeight, area.height), area.height);

  return {
    x: area.x + Math.max(0, Math.floor((area.width - width) / 2)),
    y: area.y + Math.max(0, Math.floor((area.height - height) / 2)),
    width,
    height,
  };
}

function enableWindowsDpiAwareness(): void {
  if (process.platform !== "win32") {
    return;
  }

  // Prefer per-monitor DPI awareness (Windows 8.1+).
  try {
    const shcore = dlopen("shcore.dll", {
      SetProcessDpiAwareness: {
        args: [FFIType.i32],
        returns: FFIType.i32,
      },
    });
    const PROCESS_PER_MONITOR_DPI_AWARE = 2;
    // HRESULT 0 means success; access denied usually means DPI mode was already set.
    const hr = shcore.symbols.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE);
    if (hr === 0 || hr === -2147024891) {
      console.log("[Main] DPI awareness enabled (per-monitor).");
      return;
    }
  } catch {
    // Fallback below.
  }

  // Legacy fallback for old Windows versions.
  try {
    const user32 = dlopen("user32.dll", {
      SetProcessDPIAware: {
        args: [],
        returns: FFIType.bool,
      },
    });
    const ok = user32.symbols.SetProcessDPIAware();
    if (ok) {
      console.log("[Main] DPI awareness enabled (legacy).");
    }
  } catch {
    // Best-effort only; continue even if unavailable.
  }
}

function getBackendCandidates(): string[] {
  const exeName = process.platform === "win32" ? "cccc-backend.exe" : "cccc-backend";
  const candidates: string[] = [];

  if (process.resourcesPath) {
    candidates.push(
      join(process.resourcesPath, "app", "cccc-backend", exeName),
      join(process.resourcesPath, "cccc-backend", exeName),
      join(process.resourcesPath, "resources", "cccc-backend", exeName),
    );
  }

  candidates.push(
    join(process.cwd(), "..", "Resources", "app", "cccc-backend", exeName),
    join(process.cwd(), "Resources", "app", "cccc-backend", exeName),
    join(import.meta.dir, "..", "..", "..", "Resources", "app", "cccc-backend", exeName),
    join(import.meta.dir, "..", "..", "..", "..", "dist", "cccc-backend", exeName),
    join(process.cwd(), "..", "dist", "cccc-backend", exeName),
    join(process.cwd(), "dist", "cccc-backend", exeName),
  );

  return candidates;
}

/**
 * Resolve backend executable path from packaged and development candidates.
 */
async function resolveBackendPath(): Promise<string> {
  const seen = new Set<string>();
  const tried: string[] = [];

  for (const candidate of getBackendCandidates()) {
    if (seen.has(candidate)) {
      continue;
    }
    seen.add(candidate);
    tried.push(candidate);

    if (await Bun.file(candidate).exists()) {
      return candidate;
    }
  }

  throw new Error(`Backend executable not found. Tried:\n${tried.join("\n")}`);
}

/**
 * Start the Python backend process
 */
async function startBackend(): Promise<void> {
  const backendPath = await resolveBackendPath();
  const backendDir = dirname(backendPath);

  console.log(`[Main] Starting backend: ${backendPath}`);

  // Spawn the backend process
  backendProcess = spawn({
    cmd: [backendPath],
    cwd: backendDir,
    env: {
      ...process.env,
      CCCC_WEB_PORT: String(CONFIG.backendPort),
    },
    windowsHide: process.platform === "win32",
    stdout: "pipe",
    stderr: "pipe",
  });

  // Log backend output
  backendProcess.stdout.getReader().read().then(function logStdout({ done, value }) {
    if (!done && value) {
      console.log(`[Backend] ${new TextDecoder().decode(value)}`);
    }
  });

  backendProcess.stderr.getReader().read().then(function logStderr({ done, value }) {
    if (!done && value) {
      console.error(`[Backend Error] ${new TextDecoder().decode(value)}`);
    }
  });

  console.log(`[Main] Backend started with PID: ${backendProcess.pid}`);
}

/**
 * Wait for backend to be ready
 */
async function waitForBackend(): Promise<void> {
  const startTime = Date.now();
  const url = `http://${CONFIG.backendHost}:${CONFIG.backendPort}/api/v1/ping`;

  console.log(`[Main] Waiting for backend at ${url}...`);

  while (Date.now() - startTime < CONFIG.startupTimeout) {
    try {
      const response = await fetch(url, { method: "GET" });
      if (response.ok) {
        console.log("[Main] Backend is ready!");
        return;
      }
    } catch (e) {
      // Backend not ready yet
    }

    await new Promise((r) => setTimeout(r, CONFIG.retryInterval));
  }

  throw new Error("Backend startup timeout");
}

/**
 * Stop the backend process
 */
function stopBackend(): void {
  if (backendProcess) {
    console.log("[Main] Stopping backend...");
    backendProcess.kill();
    backendProcess = null;
  }
}

function syncRendererViewport(reason: string): void {
  if (!mainWindow) {
    return;
  }
  try {
    mainWindow.webview.executeJavascript(VIEWPORT_SYNC_SCRIPT);
  } catch (error) {
    console.warn(`[Main] viewport sync failed (${reason}):`, error);
  }
}

function forceResizePulse(): void {
  if (!mainWindow) {
    return;
  }

  if (resizePulseTimer !== null) {
    clearTimeout(resizePulseTimer);
    resizePulseTimer = null;
  }

  const frame = mainWindow.getFrame();
  const canPulseWidth = frame.width > 360;
  const canPulseHeight = frame.height > 280;
  const pulseWidth = canPulseWidth ? frame.width - 1 : frame.width;
  const pulseHeight = !canPulseWidth && canPulseHeight ? frame.height - 1 : frame.height;

  try {
    if (pulseWidth === frame.width && pulseHeight === frame.height) {
      syncRendererViewport("resize-pulse-noop");
      return;
    }

    mainWindow.setFrame(frame.x, frame.y, pulseWidth, pulseHeight);

    resizePulseTimer = setTimeout(() => {
      if (!mainWindow) {
        resizePulseTimer = null;
        return;
      }

      try {
        const current = mainWindow.getFrame();
        const restoreWidth = canPulseWidth ? current.width + 1 : current.width;
        const restoreHeight = !canPulseWidth && canPulseHeight ? current.height + 1 : current.height;
        mainWindow.setFrame(current.x, current.y, restoreWidth, restoreHeight);
        syncRendererViewport("resize-pulse-restore");
      } catch (error) {
        console.warn("[Main] resize pulse restore failed:", error);
      } finally {
        resizePulseTimer = null;
      }
    }, 24);
  } catch (error) {
    console.warn("[Main] resize pulse failed:", error);
  }
}

function scheduleResizeViewportSync(delayMs = 48): void {
  if (resizeSyncTimer !== null) {
    clearTimeout(resizeSyncTimer);
  }
  resizeSyncTimer = setTimeout(() => {
    resizeSyncTimer = null;
    syncRendererViewport("window-resize");
  }, delayMs);
}

/**
 * Create the main application window
 */
function createWindow(): void {
  const url = `http://${CONFIG.backendHost}:${CONFIG.backendPort}/ui/`;
  const frame = getInitialWindowFrame();
  const isWindows = process.platform === "win32";

  mainWindow = new BrowserWindow({
    title: "CCCC",
    url: url,
    frame,
    // Platform-specific options
    ...(process.platform === "darwin" && {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: { x: 15, y: 15 },
    }),
    ...(process.platform === "win32" && {
      autoHideMenuBar: true,
    }),
  });

  // Handle window close
  mainWindow.on("closed", () => {
    if (resizeSyncTimer !== null) {
      clearTimeout(resizeSyncTimer);
      resizeSyncTimer = null;
    }
    if (resizePulseTimer !== null) {
      clearTimeout(resizePulseTimer);
      resizePulseTimer = null;
    }
    mainWindow = null;
  });

  const syncInitialLayout = (reason: string) => {
    if (!mainWindow) {
      return;
    }

    if (isWindows) {
      forceResizePulse();
    }
    syncRendererViewport(reason);
  };

  mainWindow.webview.on("dom-ready", () => {
    syncInitialLayout("dom-ready-immediate");
    setTimeout(() => syncInitialLayout("dom-ready-120ms"), 120);
    setTimeout(() => syncInitialLayout("dom-ready-500ms"), 500);
  });

  mainWindow.on("resize", () => {
    if (isWindows) {
      scheduleResizeViewportSync();
    }
  });

  console.log(`[Main] Window created (${frame.width}x${frame.height}), loading: ${url}`);
}

/**
 * Application entry point
 */
async function main() {
  console.log("[Main] CCCC Desktop starting...");
  console.log(`[Main] Platform: ${process.platform}`);
  console.log(`[Main] Resources: ${process.resourcesPath || "development"}`);

  try {
    enableWindowsDpiAwareness();

    // Start backend
    await startBackend();

    // Wait for backend to be ready
    await waitForBackend();

    // Create window
    createWindow();

    console.log("[Main] Application started successfully!");
  } catch (error) {
    console.error("[Main] Failed to start:", error);
    stopBackend();
    process.exit(1);
  }
}

// Handle app quit
process.on("beforeExit", () => {
  stopBackend();
});

process.on("SIGINT", () => {
  stopBackend();
  process.exit(0);
});

process.on("SIGTERM", () => {
  stopBackend();
  process.exit(0);
});

// Start the application
main();
