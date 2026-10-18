// Overfield developer API: a key-gated, versioned front for the plan files the site publishes.
// Deploy: supabase functions deploy api --no-verify-jwt   (keys are checked here, not by Supabase auth)
// Env (set with `supabase secrets set`): OVERFIELD_SITE (default: the GitHub Pages site). SUPABASE_URL and
// SUPABASE_SERVICE_ROLE_KEY are injected by the platform.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SITE = (Deno.env.get("OVERFIELD_SITE") ?? "https://ishaanmakam.github.io/harvest-radar/").replace(/\/?$/, "/");
const ATTRIBUTION = "Overfield (ishaanmakam/harvest-radar). Imagery: Copernicus Sentinel-2. Weather: Open-Meteo. Yields: USDA NASS.";
const LICENCE = "Plans and analytics: CC BY 4.0. Underlying sources keep their own terms; see /v1/sources.";
const CACHE_S = 300;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const cache = new Map<string, { at: number; body: unknown }>();

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...CORS, "content-type": "application/json; charset=utf-8" } });
}

async function file(name: string): Promise<unknown | null> {
  const hit = cache.get(name);
  if (hit && Date.now() - hit.at < CACHE_S * 1000) return hit.body;
  const res = await fetch(SITE + "out/" + name, { headers: { "user-agent": "Overfield-api/1" } });
  if (!res.ok) return null;
  const body = await res.json();
  cache.set(name, { at: Date.now(), body });
  return body;
}

function keyFrom(req: Request, url: URL): string | null {
  const auth = req.headers.get("authorization") ?? "";
  const m = auth.match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : url.searchParams.get("key");
}

function envelope(asOf: unknown, data: unknown) {
  return { served_at: new Date().toISOString(), as_of: asOf ?? null, attribution: ATTRIBUTION, licence: LICENCE, data };
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  if (req.method !== "GET") return json({ error: "GET only" }, 405);
  const url = new URL(req.url);
  const path = url.pathname.replace(/^.*?\/api(?=\/|$)/, "") || "/";

  if (path === "/v1/health" || path === "/") {
    const run = (await file("run.json")) as Record<string, unknown> | null;
    return json({ ok: true, as_of: run?.as_of ?? null, site: SITE, endpoints: ["/v1/plans", "/v1/farms/{farm_id}/plan", "/v1/route", "/v1/landscape", "/v1/sources"] });
  }

  const key = keyFrom(req, url);
  if (!key) return json({ error: "an API key is needed: Authorization: Bearer ovf_... (create one on the Developers page)" }, 401);
  const { data: rows, error } = await supabase.rpc("verify_api_key", { p_key: key });
  if (error) return json({ error: "key check failed" }, 500);
  const k = Array.isArray(rows) && rows.length ? rows[0] : null;
  if (!k) return json({ error: "unknown or revoked key" }, 401);

  let status = 200;
  let body: unknown;
  try {
    if (Number(k.used_today) >= Number(k.daily_limit)) {
      status = 429;
      body = { error: `daily limit of ${k.daily_limit} calls reached; it resets 24 hours after the first call` };
    } else if (path === "/v1/plans") {
      const plans = (await file("plans.json")) as Record<string, unknown> | null;
      if (!plans) { status = 503; body = { error: "plans not published yet" }; } else body = envelope(plans.as_of, plans);
    } else if (/^\/v1\/farms\/[^/]+\/plan$/.test(path)) {
      const id = decodeURIComponent(path.split("/")[3]);
      const plans = (await file("plans.json")) as { as_of?: unknown; plans?: Array<{ farm_id: string }> } | null;
      const plan = plans?.plans?.find((p) => p.farm_id === id);
      if (!plan) { status = 404; body = { error: `no plan for farm ${id}` }; } else body = envelope(plans?.as_of, plan);
    } else if (path === "/v1/route") {
      const route = (await file("route.json")) as Record<string, unknown> | null;
      if (!route) { status = 503; body = { error: "route not published yet" }; } else body = envelope(route.as_of, route);
    } else if (path === "/v1/landscape") {
      const a = (await file("analytics.json")) as Record<string, unknown> | null;
      if (!a) { status = 503; body = { error: "analytics not published yet" }; } else body = envelope(a.as_of, { landscape: a.landscape ?? null, forecast_source: a.forecast_source ?? null });
    } else if (path === "/v1/sources") {
      const res = await fetch(SITE + "data/sources.json");
      if (!res.ok) { status = 503; body = { error: "sources list not published yet" }; } else body = envelope(null, await res.json());
    } else {
      status = 404;
      body = { error: "unknown endpoint", endpoints: ["/v1/plans", "/v1/farms/{farm_id}/plan", "/v1/route", "/v1/landscape", "/v1/sources"] };
    }
  } catch (e) {
    status = 500;
    body = { error: `upstream read failed: ${(e as Error).message}` };
  }
  supabase.rpc("log_api_use", { p_key_id: k.key_id, p_path: path, p_status: status }).then(() => {}, () => {});
  return json(body, status);
});
