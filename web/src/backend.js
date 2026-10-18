// Overfield's own accounts backend: the inbound server (intelligence/inbound_server.py, routes in accounts_api.py).
// Email and password sign-in, a bearer session token kept in this browser, the profile that becomes the morning
// roster, developer keys, and the admin pages. No third-party service: the same server answers the webhooks.
const env = import.meta.env || {};
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
const localDefault = typeof window !== "undefined" && LOCAL_HOSTS.has(window.location.hostname) ? "http://localhost:8788" : "";

export const API_BASE = String(env.VITE_API_BASE || "").trim().replace(/\/+$/, "") || localDefault;
export const API_BASE_PLACEHOLDER = "https://<your-tunnel>.trycloudflare.com";
export const TERMS_VERSION = "developer-v1";
export const configured = Boolean(API_BASE);
export const NOT_CONFIGURED =
  "Accounts are not set up on this copy of Overfield yet: the site does not know where its server is. Start the inbound server and publish its address (backend/README.md).";

const TOKEN_KEY = "overfield.session";
const listeners = new Set();
let token = null;
let currentUser = null;
let initialised = false;

try {
  token = window.localStorage.getItem(TOKEN_KEY) || null;
} catch {
  token = null;
}

function remember(value) {
  token = value;
  try {
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage off */
  }
}

function need() {
  if (!configured) throw new Error(NOT_CONFIGURED);
  return API_BASE;
}

/** One call to the server: JSON in and out, the session token as a bearer, errors as plain sentences. */
export async function api(method, path, body, { key } = {}) {
  const base = need();
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers.Authorization = `Bearer ${key}`;
  else if (token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(`${base}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new Error("The Overfield server did not answer. Check that it is running and that the tunnel is up.");
  }
  const type = res.headers.get("content-type") || "";
  const payload = type.includes("json") ? await res.json().catch(() => ({})) : await res.text();
  if (res.status === 401 && !key && token && path !== "/auth/login") {
    // the session is gone on the server (expired, or the account was deleted): forget it here too
    setUser(null, "SIGNED_OUT");
  }
  if (!res.ok) {
    const message = (payload && payload.error) || (typeof payload === "string" && payload) || `${res.status} from the server`;
    throw new Error(message);
  }
  return payload;
}

function setUser(user, event) {
  const changed = (user?.id || null) !== (currentUser?.id || null);
  currentUser = user || null;
  if (!user) remember(null);
  for (const cb of listeners) cb(currentUser ? { user: currentUser } : null, event);
  return changed;
}

/** Who is signed in, checked once against the server; later calls answer from memory. */
export async function getSession() {
  if (!configured) return null;
  if (!initialised) {
    initialised = true;
    if (token) {
      try {
        const doc = await api("GET", "/auth/me");
        currentUser = doc.account;
      } catch {
        currentUser = null;
        remember(null);
      }
    }
  }
  return currentUser ? { user: currentUser } : null;
}

/** cb(session, event) now with the stored session (event INITIAL_SESSION), then on every sign-in and sign-out. */
export function onAuth(cb) {
  listeners.add(cb);
  getSession().then((session) => cb(session, "INITIAL_SESSION"));
  return () => listeners.delete(cb);
}

export async function signIn(email, password) {
  const doc = await api("POST", "/auth/login", { email: String(email || "").trim().toLowerCase(), password });
  remember(doc.token);
  setUser(doc.account, "SIGNED_IN");
  return doc.account;
}

export async function signUp(email, password, name) {
  const doc = await api("POST", "/auth/signup", { email: String(email || "").trim().toLowerCase(), password, name: String(name || "").trim() });
  remember(doc.token);
  setUser(doc.account, "SIGNED_IN");
  return doc.account;
}

export async function signOut() {
  if (token) await api("POST", "/auth/logout").catch(() => {});
  setUser(null, "SIGNED_OUT");
}

/** The signed-in person's account row: the profile and the roster fields are the same record. */
export async function myProfile() {
  const doc = await api("GET", "/auth/me");
  currentUser = doc.account;
  return doc.account;
}

export async function updateMyProfile(_userId, patch) {
  const doc = await api("PATCH", "/me", patch);
  currentUser = doc.account;
  return doc.account;
}

export const myDeliveries = () => api("GET", "/me/deliveries").then((d) => d.deliveries || []);
export const deleteMyAccount = () => api("POST", "/me/delete");
export const termsStatus = () => api("GET", "/terms");
export const acceptTerms = (version = TERMS_VERSION, kind = "developer") => api("POST", "/terms/accept", { version, kind });
export const listKeys = () => api("GET", "/keys").then((d) => d.keys || []);
export const issueApiKey = (name) => api("POST", "/keys", { name: String(name || "").trim() || "default" }).then((d) => d.key);
export const revokeApiKey = (id) => api("DELETE", `/keys/${id}`);

export const adminOverview = () => api("GET", "/admin/overview");
export const adminAccounts = () => api("GET", "/admin/accounts").then((d) => d.accounts || []);
export const adminUpdate = (id, patch) => api("PATCH", `/admin/accounts/${id}`, patch).then((d) => d.account);
export const adminDeliveries = (runDate) => api("GET", `/admin/deliveries${runDate ? `?run_date=${encodeURIComponent(runDate)}` : ""}`);
export const adminKeys = () => api("GET", "/admin/keys").then((d) => d.keys || []);
export const adminExportCsv = () => api("GET", "/admin/export.csv");
