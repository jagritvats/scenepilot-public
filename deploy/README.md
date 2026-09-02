# Deploying ScenePilot to Cloud Run

Two services: `scenepilot-agent` (FastAPI + ADK) and `scenepilot-web` (Next.js), both in one region.
`deploy/cloudrun.sh` does the deploy; everything below it is one-time setup.

Run from the **repo root**, in Git Bash on Windows (the script uses `services/agent` and `apps/web`
as relative paths).

## One-time setup

```bash
cd /d/dev/projects/nightfall
export PROJECT_ID=nifty-expanse-477010-t3
export REGION=asia-south1

# 1. APIs. If billing is not enabled on the project, this is where it fails — fix that first.
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com artifactregistry.googleapis.com

# 2. The two keys, piped straight from .env so they are never printed to a terminal or a log.
grep -m1 '^GOOGLE_API_KEY='   .env | cut -d= -f2- | tr -d '\r\n' | gcloud secrets create GOOGLE_API_KEY   --data-file=-
grep -m1 '^PARALLEL_API_KEY=' .env | cut -d= -f2- | tr -d '\r\n' | gcloud secrets create PARALLEL_API_KEY --data-file=-

# 3. Let the Cloud Run runtime service account read them.
PN=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
for S in GOOGLE_API_KEY PARALLEL_API_KEY; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done
```

To rotate a key later, add a version rather than recreating the secret — the services read `:latest`:

```bash
printf '%s' "$NEW_KEY" | gcloud secrets versions add GOOGLE_API_KEY --data-file=-
```

## Deploy

```bash
./deploy/cloudrun.sh
```

The first run prompts once to create an Artifact Registry repository (`cloud-run-source-deploy`).
Answer **Y**. It is interactive, so do not background it.

The script deploys the agent, reads back its URL, updates the agent a second time with
`PUBLIC_BASE_URL` (a service cannot know its own URL until it exists, and Parallel monitor webhooks
have nowhere to land without it), then deploys the web app pointed at the agent.

## Verify

```bash
AGENT=$(gcloud run services describe scenepilot-agent --region "$REGION" --format 'value(status.url)')
WEB=$(gcloud run services describe scenepilot-web   --region "$REGION" --format 'value(status.url)')

curl -s "$AGENT/api/health"   | python -m json.tool   # mode: live, and the six Parallel APIs
curl -s "$AGENT/api/features" | python -m json.tool   # task/memory/findall/monitors all enabled
echo "$WEB/projects/proj_nightfall/days/day_4"
```

Then walk the hero path by hand — Day 4 → the rain fixture → recovery options → approve → call
sheet. A revision can report `Ready=True` while the public URL returns a Google 404; that is almost
always a container not listening on `$PORT` at `0.0.0.0`, which both Dockerfiles now do.

## What the defaults mean

| Setting | Why |
|---|---|
| `--max-instances 1` (agent) | Correctness, not thrift. Without `DATABASE_URL` the store is SQLite under `/tmp`, which is per-instance — a second instance would serve a second, divergent production, and a recovery approved on one would not exist on the other. |
| `--no-cpu-throttling` | Both orchestrators are fire-and-forget `asyncio` tasks started *after* the HTTP response returns. Under default throttling the CPU is taken away between requests, so a rescue run would only advance while a browser happened to be polling it. |
| `--timeout 900` | A Parallel Task dossier takes 1–5 minutes; a pre-flight re-check runs several concurrently. |
| `SCENEPILOT_MODE=live` | Live Gemini and Parallel calls are the point. `SCENEPILOT_FALLBACK_TO_RECORDING=1` (on by default in code) serves a labelled recording if a call fails, so a judge's click never dead-ends. |
| `SCENEPILOT_PARALLEL_{TASK,MEMORY,FINDALL}=1` | Off by default in code so nothing expensive fires implicitly. On a hosted demo they must be on, or dossiers, memory, substitutes, monitors and the pre-flight all return 501 and the Parallel integration is invisible. |
| `SCENEPILOT_PAID_CALL_BUDGET=40` | The URL sits on a public Devpost page for weeks with no auth, and every paid feature is one unauthenticated POST away. Past the ceiling the API returns a priced, explained refusal — the same shape the UI already renders for a disabled feature — not an error. |
| `SCENEPILOT_ALLOW_WRAP=0`, `SCENEPILOT_ALLOW_COMMIT_BOARD=0` | The inverse of the Parallel flags, for the inverse reason. Both are **on** in code because a local clone owns its own database; both are off here because the same unauthenticated URL applies. Wrapping a day writes a record that cannot be rescued, re-timed or re-wrapped, and only a full reset undoes it — one visitor could end the demo for every visitor after them. `GET /api/features` still reports them, so the controls render disabled with the variable that opens them rather than vanishing. |
| `GOOGLE_GENAI_USE_VERTEXAI=TRUE` | This project's Gemini key is a Vertex express-mode key (`AQ.` prefix), which 403s otherwise. A plain AI Studio key (`AIza…`) needs `FALSE`. |

`.gcloudignore` in `services/agent` and `apps/web` keeps `.venv`, `node_modules`, `.next` and the
local `scenepilot.db` out of the Cloud Build upload — roughly 1.7 GB per deploy. `.dockerignore` is
not read at that stage, so both files are needed.

## State, and the demo staying fresh

Without `DATABASE_URL` the agent keeps state in SQLite under `/tmp`, so a new revision starts clean.
That is survivable because the warm demo seed (`SCENEPILOT_WARM_DEMO=1`, the default) replays the
bundled recordings on boot: the hero screenplay and every location dossier come back, labelled
`replayed`, accepted by nobody. For state that genuinely survives a redeploy — accepted facts,
applied ChangeSets — point `DATABASE_URL` at Cloud SQL Postgres.

Either way the agent re-anchors Shoot Day 4 onto today at boot **and on every project read**, using
Asia/Kolkata rather than the container's UTC, so a revision left running through judging never opens
on a hero day in the past.

## Cost control and teardown

```bash
curl -s "$AGENT/api/features" | python -m json.tool          # .budget shows spend against the ceiling
```

Parallel **monitors bill daily until cancelled**, and they are the one paid object that outlives the
demo. A reset cancels every live monitor first; to cancel one directly:

```bash
curl -X POST "$AGENT/api/projects/proj_nightfall/monitors/<monitor_id>/cancel"
```

When judging is over:

```bash
gcloud run services delete scenepilot-web scenepilot-agent --region "$REGION"
```

## Public surface

`/docs`, `/redoc` and `/openapi.json` are disabled (`SCENEPILOT_DEV=1` re-enables them), and no
cross-origin caller is allowed — the browser reaches the agent same-origin through the Next proxy at
`apps/web/src/app/api/[...path]/route.ts`, which reads `AGENT_URL` per request. Set
`SCENEPILOT_ALLOWED_ORIGINS=https://…` only if something else must call the agent directly.
