import { request as httpRequest, type IncomingHttpHeaders } from "node:http";
import { request as httpsRequest } from "node:https";
import { type NextRequest, NextResponse } from "next/server";

/**
 * Runtime proxy from the browser to the agent service.
 *
 * This used to be a `rewrites()` entry in next.config.ts, which does not survive a container build:
 * Next resolves rewrites at **build** time into the routes manifest, so `AGENT_URL` was baked in as
 * `http://localhost:8000` and a deployed web service proxied `/api/*` straight back at itself. A
 * route handler reads the variable per request instead, so the same image can be pointed at any
 * agent URL with an ordinary environment variable — which is exactly what Cloud Run's
 * `--set-env-vars` provides, and what the deploy script assumed all along.
 *
 * Server components bypass this entirely (`api.ts` calls AGENT_URL directly when there is no
 * window), so this path only carries browser traffic: whole request/response bodies, no streaming.
 *
 * `node:http` rather than `fetch`, because a Parallel Task run answers in minutes and undici's
 * global fetch caps a silent upstream at its own 300s headers timeout regardless of `maxDuration`.
 */

const AGENT_URL = () => process.env.AGENT_URL || "http://localhost:8000";

// Dossiers and pre-flight re-checks are real Parallel Task runs — minutes, not milliseconds.
export const maxDuration = 800;
export const dynamic = "force-dynamic";

const BUDGET_MS = maxDuration * 1000;

// `content-encoding` / `content-length` are deliberately absent: the body is re-buffered here.
const FORWARDED = ["content-type", "content-disposition", "content-language", "cache-control", "location"];

interface Upstream {
  status: number;
  headers: IncomingHttpHeaders;
  body: Buffer;
}

function send(target: URL, method: string, headers: Record<string, string>, body: Buffer | null): Promise<Upstream> {
  return new Promise((resolve, reject) => {
    const call = target.protocol === "https:" ? httpsRequest : httpRequest;
    const req = call(target, { method, headers }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (chunk: Buffer) => chunks.push(chunk));
      res.on("end", () => resolve({ status: res.statusCode ?? 502, headers: res.headers, body: Buffer.concat(chunks) }));
      res.on("error", reject);
    });
    req.setTimeout(BUDGET_MS, () => req.destroy(new Error(`no response within ${maxDuration}s`)));
    req.on("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const target = new URL(`/api/${path.join("/")}`, AGENT_URL());
  target.search = req.nextUrl.search;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const body = hasBody ? Buffer.from(await req.text()) : null;

  const headers: Record<string, string> = {};
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;
  if (body) headers["content-length"] = String(body.byteLength);

  try {
    const upstream = await send(target, req.method, headers, body);
    const out = new Headers();
    for (const name of FORWARDED) {
      const value = upstream.headers[name];
      if (value) out.set(name, Array.isArray(value) ? value.join(", ") : value);
    }
    if (!out.has("content-type")) out.set("content-type", "application/json");
    return new NextResponse(new Uint8Array(upstream.body), { status: upstream.status, headers: out });
  } catch (err) {
    // A dead agent should read as a dead agent, not as a broken web app — and the address of the
    // agent is the operator's business, not the browser's.
    console.error(`[proxy] ${req.method} ${target.href} failed:`, err);
    return NextResponse.json(
      { detail: "the agent service is not responding yet" },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
