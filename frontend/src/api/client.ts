import axios from "axios";

import { installAxiosLogging } from "../lib/diagnostics";

const api = axios.create({
  baseURL: "/api",
  headers: { "Content-Type": "application/json" },
});

// Request: attach JWT.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("baulv_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response: handle auth expiry. 401 means the server told us the
// session is dead (revoked, expired, or legacy-pre-jti). Drop the
// token and bounce to /login — but only from pages where that makes
// sense. Don't redirect public pages, auth pages, or profile (which
// handles 401 on its own gracefully after self-revoke).
api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("baulv_token");
      const p = window.location.pathname;
      const isPublic =
        p === "/" ||
        p === "/login" ||
        p === "/register" ||
        p === "/password-reset" ||
        p.startsWith("/impressum") ||
        p.startsWith("/datenschutz") ||
        p.startsWith("/agb");
      if (!isPublic) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

// Install diagnostic logging last so it runs before the interceptors
// above on response (interceptors fire in reverse order for responses).
// Every request/response/error will log its method, URL, status, and
// duration to the console — essential for "the button does nothing"
// bug reports.
installAxiosLogging(api);

export default api;
