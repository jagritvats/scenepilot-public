#!/usr/bin/env bash
# Deploy ScenePilot to Cloud Run (two services). Requires: gcloud auth, a project, billing.
#
#   PROJECT_ID=my-proj REGION=asia-south1 ./deploy/cloudrun.sh
#
# Create the secrets once, before the first run:
#   printf '%s' "$GOOGLE_API_KEY"   | gcloud secrets create GOOGLE_API_KEY   --data-file=-
#   printf '%s' "$PARALLEL_API_KEY" | gcloud secrets create PARALLEL_API_KEY --data-file=-
# ...and grant the runtime service account access:
#   gcloud secrets add-iam-policy-binding GOOGLE_API_KEY   --member="serviceAccount:$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
#   gcloud secrets add-iam-policy-binding PARALLEL_API_KEY --member="serviceAccount:$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.5-flash}"

# A Vertex AI express-mode key (prefix `AQ.`) 403s unless this is TRUE; a plain AI Studio key needs
# it FALSE. The project's own key is express mode, so that is the default.
USE_VERTEX="${GOOGLE_GENAI_USE_VERTEXAI:-TRUE}"

# The paid Parallel features are off by default in code — deliberately, so nothing expensive can
# fire implicitly. On a hosted demo they must be ON, or dossiers, memory, substitutes, monitors and
# the pre-flight re-check all return 501 and the entire Parallel integration is invisible.
TASK="${SCENEPILOT_PARALLEL_TASK:-1}"
MEMORY="${SCENEPILOT_PARALLEL_MEMORY:-1}"
FINDALL="${SCENEPILOT_PARALLEL_FINDALL:-1}"

# Live calls are the point of the demo: Gemini and Parallel are exercised at runtime, not from canned data.
# `SCENEPILOT_FALLBACK_TO_RECORDING` (on by default in code) keeps a demo alive if a call fails.
MODE="${SCENEPILOT_MODE:-live}"

# The hosted URL sits on a public Devpost page for weeks with no auth, and every one of those paid
# features is one unauthenticated POST away. This is the ceiling the service will actually spend
# against before it starts refusing (as a priced, explained refusal, not an error).
PAID_BUDGET="${SCENEPILOT_PAID_CALL_BUDGET:-40}"
PAID_COOLDOWN="${SCENEPILOT_PAID_CALL_COOLDOWN_S:-60}"

# The two writes a producer cannot take back: closing a day out, and keeping a hand-edited board.
# Both are ON in code, because a local clone owns its own database and is the product. Neither is on
# here, for the reason stated above about this URL: an unauthenticated visitor who wraps Day 4 leaves
# a record the next visitor cannot undo without resetting the whole production. Unset them (or set
# them to 1) for a private deployment you intend to actually shoot against.
ALLOW_WRAP="${SCENEPILOT_ALLOW_WRAP:-0}"
ALLOW_COMMIT_BOARD="${SCENEPILOT_ALLOW_COMMIT_BOARD:-0}"

gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com >/dev/null

AGENT_ENV="GEMINI_MODEL=$GEMINI_MODEL"
AGENT_ENV="$AGENT_ENV,GOOGLE_GENAI_USE_VERTEXAI=$USE_VERTEX"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_MODE=$MODE"
AGENT_ENV="$AGENT_ENV,PARALLEL_SEARCH_MODE=${PARALLEL_SEARCH_MODE:-advanced}"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_PARALLEL_TASK=$TASK"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_PARALLEL_MEMORY=$MEMORY"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_PARALLEL_FINDALL=$FINDALL"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_PAID_CALL_BUDGET=$PAID_BUDGET"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_PAID_CALL_COOLDOWN_S=$PAID_COOLDOWN"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_ALLOW_WRAP=$ALLOW_WRAP"
AGENT_ENV="$AGENT_ENV,SCENEPILOT_ALLOW_COMMIT_BOARD=$ALLOW_COMMIT_BOARD"
AGENT_ENV="$AGENT_ENV${DATABASE_URL:+,DATABASE_URL=$DATABASE_URL}"

echo "→ building + deploying agent service"
# --timeout 900: a Parallel Task dossier takes 1–5 minutes, and a pre-flight re-check runs several.
#
# --max-instances 1 is a correctness requirement, not thrift: without DATABASE_URL the store is
# SQLite under /tmp, which is per-instance, so a second instance would serve a second, divergent
# production — approve a recovery on one and reload onto the other and it never happened.
#
# --no-cpu-throttling likewise: both orchestrators are fire-and-forget `asyncio.create_task`s
# started *after* the HTTP response returns. Under the default throttling the CPU is taken away
# between requests, so a rescue run would only advance while a browser happened to be polling it.
gcloud run deploy scenepilot-agent \
  --source services/agent --region "$REGION" --allow-unauthenticated \
  --set-env-vars "$AGENT_ENV" \
  --set-secrets "GOOGLE_API_KEY=GOOGLE_API_KEY:latest,PARALLEL_API_KEY=PARALLEL_API_KEY:latest" \
  --memory 1Gi --cpu 1 --min-instances 1 --max-instances 1 --no-cpu-throttling --timeout 900
AGENT_URL=$(gcloud run services describe scenepilot-agent --region "$REGION" --format 'value(status.url)')
echo "agent: $AGENT_URL"

# Second pass, because the service cannot know its own URL until it exists. Without this, Parallel
# has nowhere to deliver monitor webhooks, so "Watch for changes" and live monitors both refuse.
echo "→ pointing the agent's webhook at itself"
gcloud run services update scenepilot-agent --region "$REGION" \
  --update-env-vars "PUBLIC_BASE_URL=$AGENT_URL" >/dev/null
echo "webhook: $AGENT_URL/api/webhooks/parallel"

echo "→ building + deploying web app"
# AGENT_URL is read per request by src/app/api/[...path]/route.ts, so it is a runtime variable and
# the same image can be repointed. (It used to be a next.config rewrite, which baked localhost in.)
# --max-instances 1 here too: the web tier is only a proxy in front of a single-instance agent, so
# more of it buys nothing and just widens the blast radius of a public URL.
gcloud run deploy scenepilot-web \
  --source apps/web --region "$REGION" --allow-unauthenticated \
  --set-env-vars "AGENT_URL=$AGENT_URL" \
  --memory 512Mi --min-instances 1 --max-instances 1 --timeout 900
WEB_URL=$(gcloud run services describe scenepilot-web --region "$REGION" --format 'value(status.url)')

echo
echo "web:   $WEB_URL"
echo "agent: $AGENT_URL"
echo
echo "Verify (all four of memory/task/findall/monitors should be enabled — monitors only turns on"
echo "once PUBLIC_BASE_URL is set, which the second pass above does):"
echo "  curl -s $AGENT_URL/api/health | jq '{mode, features: [.parallel_features|to_entries[]|select(.value.enabled)|.key]}'"
echo "  open $WEB_URL/projects/proj_nightfall/days/day_6"
echo
echo "Note: without DATABASE_URL the agent uses SQLite under /tmp, so state resets on a new revision."
echo "      --min-instances 1 keeps one warm instance, which is enough for a demo, and a reset instance"
echo "      re-seeds itself from the bundled recordings (SCENEPILOT_WARM_DEMO=1, the default): the hero"
echo "      screenplay and every location dossier come back, labelled replayed and accepted by nobody."
echo "      For state that genuinely survives — accepted facts, applied ChangeSets — set DATABASE_URL to"
echo "      a Cloud SQL Postgres connection string. (Either way the agent re-anchors Shoot Day 4 onto"
echo "      today when it boots and on every project read, so a revision left running for a fortnight"
echo "      never opens on a hero day in the past. Today means today in Asia/Kolkata, not on the UTC"
echo "      container, so the Mumbai day is not off by one before 05:30 IST.)"
echo
echo "Public surface: /docs, /redoc and /openapi.json are off (set SCENEPILOT_DEV=1 to serve them) and"
echo "no cross-origin caller is allowed — the browser reaches the agent same-origin through the Next"
echo "proxy. Set SCENEPILOT_ALLOWED_ORIGINS=https://… only if something else must call it directly."
echo "Spend ceiling: $PAID_BUDGET priced Parallel calls per 24 h, ${PAID_COOLDOWN}s between two identical ones."
echo "  curl -s $AGENT_URL/api/features | jq .budget"
echo "Monitors bill daily until cancelled: POST $AGENT_URL/api/projects/proj_nightfall/monitors/<id>/cancel"
echo "(a reset cancels every live monitor first). To take the demo down entirely when judging is over:"
echo "  gcloud run services delete scenepilot-web scenepilot-agent --region $REGION"
