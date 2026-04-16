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
// Two things matter here. The first is registering the SW so the
// app works as a PWA. The second is getting users off the old SW
// when we deploy a new one. Without the `controllerchange` listener,
// a user who had v1 installed would never pick up v3 until they
// manually killed every tab and reopened the app — which is exactly
// how production ended up running stale bundles after each deploy.
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
