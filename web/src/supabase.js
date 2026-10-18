// Supabase from the browser. The anon key is public by design; row-level security in
// backend/supabase/migrations/0001_init.sql is the fence. Nothing here runs when VITE_SUPABASE_URL is
// unset: `configured` is false and the account, developer and admin pages show a notice instead.
import { createClient } from "@supabase/supabase-js";

const env = import.meta.env;

export const SUPABASE_URL = String(env.VITE_SUPABASE_URL || "").trim().replace(/\/+$/, "");
const ANON_KEY = String(env.VITE_SUPABASE_ANON_KEY || "").trim();

export const API_BASE_PLACEHOLDER = "https://<your-project>.supabase.co/functions/v1/api";
/** The developer API base: VITE_API_BASE, else derived from the project URL, else empty (the docs show the placeholder). */
export const API_BASE = String(env.VITE_API_BASE || "").trim().replace(/\/+$/, "") || (SUPABASE_URL ? `${SUPABASE_URL}/functions/v1/api` : "");
export const TERMS_VERSION = "developer-v1";
export const configured = Boolean(SUPABASE_URL && ANON_KEY);
export const NOT_CONFIGURED = "Accounts are not set up on this copy of Overfield yet. The roster is a CSV outside git; see backend/README.md.";

export const client = configured
  ? createClient(SUPABASE_URL, ANON_KEY, {
      auth: { flowType: "implicit", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    })
  : null;

export const PROFILE_COLUMNS =
  "id,roster_id,created_at,updated_at,name,phone,email,language,role,farm_id,address,lon,lat,send_time,timezone,channels,sms_consent_at,paused";
// Never key_hash: the migration revokes that column from the site, so selecting it would fail the whole query.
export const KEY_COLUMNS = "id,user_id,name,key_prefix,scopes,daily_limit,created_at,last_used_at,revoked_at";

function need() {
  if (!client) throw new Error(NOT_CONFIGURED);
  return client;
}

function fail(error) {
  const message = error?.message || error?.details || error?.hint || String(error);
  throw new Error(message);
}

export async function getSession() {
  if (!client) return null;
  const { data, error } = await client.auth.getSession();
  if (error) return null;
  return data?.session || null;
}

/** cb(session, event) on every auth change, including the initial session. Returns an unsubscribe function. */
export function onAuth(cb) {
  if (!client) return () => {};
  const { data } = client.auth.onAuthStateChange((event, session) => cb(session, event));
  return () => data?.subscription?.unsubscribe?.();
}

/** Magic link: the email carries a link back to this page (without its hash), which signs the person in. */
export async function signInWithEmail(email) {
  const redirect = window.location.href.split("#")[0];
  const { error } = await need().auth.signInWithOtp({
    email: String(email || "").trim().toLowerCase(),
    options: { emailRedirectTo: redirect, shouldCreateUser: true },
  });
  if (error) fail(error);
}

export async function signOut() {
  if (!client) return;
  const { error } = await client.auth.signOut();
  if (error && !/session/i.test(error.message || "")) {
    // The server may already have forgotten the user (deleted account); the local session still has to go.
    await client.auth.signOut({ scope: "local" }).catch(() => {});
  }
}

/** The signed-in person's profiles row. The trigger creates it at sign-up; a missing row is created here. */
export async function myProfile(user) {
  const db = need();
  if (!user?.id) return null;
  const { data, error } = await db.from("profiles").select(PROFILE_COLUMNS).eq("id", user.id).maybeSingle();
  if (error) fail(error);
  if (data) return data;
  const seed = { id: user.id, email: user.email || null, phone: user.phone || null };
  const { error: insertError } = await db.from("profiles").insert(seed);
  if (insertError) fail(insertError);
  const again = await db.from("profiles").select(PROFILE_COLUMNS).eq("id", user.id).maybeSingle();
  if (again.error) fail(again.error);
  return again.data;
}

/** Update the person's own row (role and roster_id stay with admins; the trigger refuses them). */
export async function updateMyProfile(userId, patch) {
  const { data, error } = await need().from("profiles").update(patch).eq("id", userId).select(PROFILE_COLUMNS).single();
  if (error) fail(error);
  return data;
}

export async function rpc(name, args = {}) {
  const { data, error } = await need().rpc(name, args);
  if (error) fail(error);
  return data;
}

export const acceptTerms = (version = TERMS_VERSION, kind = "developer") =>
  rpc("accept_terms", { p_version: version, p_kind: kind, p_user_agent: String(navigator.userAgent || "").slice(0, 300) });

export const issueApiKey = (name) => rpc("issue_api_key", { p_name: String(name || "").trim() || "default" });

export const revokeApiKey = (id) => rpc("revoke_api_key", { p_id: id });

export const deleteMyAccount = () => rpc("delete_my_account");
