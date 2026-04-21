import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./hooks/useAuth";
import App from "./App";
import "./index.css";
import { installGlobalErrorHandlers } from "./lib/diagnostics";

// Catch anything that slips past component-level error handling.
// Installed before React mounts so errors during initial render are
// captured too.
installGlobalErrorHandlers();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);

// Service-worker lifecycle.
//
// The v1 SW in the wild caches index.html under a fixed name that
// never gets invalidated. Users stuck on v1 never receive any
// subsequent deploy because their browser keeps serving the stale
// HTML. We deal with that in three layers:
//
//   1. A **kill switch** (below) that runs once per client: it checks
//      a localStorage marker and, the first time a client sees
//      APP_BUILD_TAG change, actively unregisters every registered SW
//      and wipes every Cache Storage bucket. The subsequent reload
//      then fetches index.html fresh from the network (which the
//      backend serves with Cache-Control: no-cache).
//
//   2. The new SW file (`sw.js` at `CACHE_NAME = "baulv-v4"`) uses
//      network-first for HTML, so even if someone keeps it installed,
//      deploys are visible on the next reload.
//
//   3. ``controllerchange`` triggers a guarded reload so the moment
//      the new SW activates, the page reloads to pick up the new JS
//      bundle without a user needing to do anything.
//
// Every time we ship a fix that users claim they don't see, bump
// APP_BUILD_TAG. The kill switch runs again, every stale SW gets
// evicted, every stale cache gets wiped.

// Whenever this changes, bump `CACHE_NAME` in ``public/sw.js`` to the
// same value. The SW's activate handler deletes any cache whose name
// doesn't match its own CACHE_NAME — matching the two tags guarantees
// the kill-switch purge and the SW cache eviction fire on the same
// deploy, so users never end up with a fresh HTML pointing at a SW
// that's still serving the previous bundle's assets from cache.
const APP_BUILD_TAG = "baulv-v8-2026-04-21-chatbots";

async function purgeStaleCaches() {
  if ("caches" in window) {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    } catch {
      /* best-effort */
    }
  }
  if ("serviceWorker" in navigator) {
    try {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    } catch {
      /* best-effort */
    }
  }
}

(() => {
  const KEY = "baulv_build_tag";
  const stored = localStorage.getItem(KEY);
  if (stored !== APP_BUILD_TAG) {
    // First time this build has run in this browser. Purge every
    // stale cache/SW and reload exactly once. We persist the new tag
    // BEFORE reloading so the reload doesn't come back here and loop.
    localStorage.setItem(KEY, APP_BUILD_TAG);
    if (stored !== null) {
      // Only reload if there was a *previous* tag — on a brand-new
      // install there's nothing to purge, so skip the reload.
      void purgeStaleCaches().then(() => {
        window.location.reload();
      });
    }
  }
})();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });

  // When a new SW takes over (because we bumped CACHE_NAME and the
  // old one was unregistered), force a reload so the new index.html
  // and its fresh bundle are what the user actually runs. Guard with
  // `refreshing` so we don't loop — controllerchange can fire more
  // than once during SW updates.
  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing) return;
    refreshing = true;
    window.location.reload();
  });
}
