"""Feature gating for the deep-Parallel APIs (Task, FindAll, Memory).

Invariant for this whole layer: **no Parallel call that costs more than a Search, or takes longer
than a few seconds, may fire implicitly.** Search and Extract are cheap and sub-second, so the
workflows call them freely. Task, FindAll and Memory are different: they are slower, they cost real
money per run, and one of them (Memory) mutates server-side state. So each is

  * behind an environment flag that is **off by default**,
  * reachable only from an explicit, named UI action, and
  * surfaced to the web app through `GET /api/features` so the button can be rendered *disabled with
    the reason*, rather than hidden — a visitor clicking around should still see the capability exists.

This is the same honesty the app already applies to production changes (nothing is applied without
producer approval), extended to spend and latency.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..config import Settings, settings as default_settings
from ..services.budget import call_budget

# feature name → the env var that turns it on, and what it costs when it does
FEATURE_ENV = {
    "memory": ("SCENEPILOT_PARALLEL_MEMORY", "Parallel Memory (beta) — no per-call cost"),
    "task": ("SCENEPILOT_PARALLEL_TASK", "Parallel Task API — ~$0.025 per location dossier, 1–5 min"),
    "findall": ("SCENEPILOT_PARALLEL_FINDALL", "Parallel FindAll / Entity Search — ~$0.005–$0.50 per run"),
}


# capability name → the env var that opens it, and what it does to the production when it is open.
#
# A second registry rather than three more entries above, because these are not Parallel and must not
# be reported as such: `GET /api/health` builds `parallel_apis` out of `Settings.parallel_features`,
# so folding a shoot-day wrap in there would have the service advertise it as a Parallel API — in the
# one payload a judge greps, on an entry judged on its Parallel integration.
CAPABILITY_ENV = {
    "wrap": ("SCENEPILOT_ALLOW_WRAP", "Closes a shoot day out. Irreversible from the UI — a wrapped day is a record."),
    "commit_board": ("SCENEPILOT_ALLOW_COMMIT_BOARD", "Commits a hand-edited board over the engine's schedule, re-validated but not proposed."),
}


def feature_state(settings: Settings | None = None) -> dict[str, dict[str, object]]:
    """What `GET /api/features` reports: every feature, whether it is on, and how to turn it on.

    One flat dict keyed by name, carrying both groups. `kind` is what tells them apart — the web
    reads an entry by name and prints `env` and `cost` whatever it is, so a capability renders
    disabled-with-the-reason through exactly the same three lines a Parallel feature does.
    """
    s = settings or default_settings
    enabled = s.parallel_features
    state: dict[str, dict[str, object]] = {}
    for name, (env, cost) in FEATURE_ENV.items():
        state[name] = {
            "enabled": name in enabled,
            "env": f"{env}=1",
            "cost": cost,
            "requires_key": not s.parallel_configured,
            "kind": "parallel",
        }
    # Monitors are not flagged (they predate this layer) but they do need a reachable webhook.
    state["monitors"] = {
        "enabled": bool(s.public_base_url and s.parallel_configured),
        "env": "PUBLIC_BASE_URL=https://…",
        "cost": "Parallel Monitor — ~$0.07/day per hourly lite monitor",
        "requires_key": not s.parallel_configured,
        "kind": "parallel",
    }
    for name, (env, consequence) in CAPABILITY_ENV.items():
        state[name] = {
            "enabled": name in s.write_capabilities,
            "env": f"{env}=1",
            # Reused rather than renamed: the web already prints this field under every gated
            # control, and what a producer needs to read before an irreversible write is the
            # consequence, which is what a cost sentence is for a paid one.
            "cost": consequence,
            "requires_key": False,  # these call nobody
            "kind": "write",
        }
    return state


def require_feature(name: str, settings: Settings | None = None) -> None:
    """Raise 501 with the exact env var to set, so the UI can explain itself without guessing."""
    s = settings or default_settings
    if name in s.parallel_features:
        return
    env, cost = FEATURE_ENV.get(name, (f"SCENEPILOT_PARALLEL_{name.upper()}", ""))
    raise HTTPException(
        501,
        detail={
            "feature": name,
            "env": f"{env}=1",
            "cost": cost,
            "message": f"The Parallel {name} integration is disabled. Set {env}=1 to enable it.",
        },
    )


def require_capability(name: str, settings: Settings | None = None) -> None:
    """Raise 501 with the exact env var to set — the same shape as `require_feature`, a different reason.

    `require_feature` is about spend and latency: a Parallel call that costs money or takes minutes
    may not fire implicitly. This is about a write a producer cannot take back. The two are kept
    apart so `/api/health` never claims a shoot-day wrap is a Parallel API, and so the flags can
    default opposite ways — a paid call is off until someone pays for it, an irreversible local
    write is on until a deployment on a public URL closes it.
    """
    s = settings or default_settings
    if name in s.write_capabilities:
        return
    env, consequence = CAPABILITY_ENV[name]
    raise HTTPException(
        501,
        detail={
            "feature": name,
            "env": f"{env}=1",
            "cost": consequence,
            "message": f"This deployment does not allow {name.replace('_', ' ')}. Set {env}=1 to enable it.",
        },
    )


def require_budget(name: str, subject: str, units: int = 1, settings: Settings | None = None) -> None:
    """Book a priced call against the deployment's budget, or refuse it the way a disabled feature is.

    Called as late as possible in a handler — after the cheap validation, immediately before the
    spend — so a request that was going to 404 anyway never costs anyone a slot.
    """
    refusal = call_budget.charge(name, subject, units, settings or default_settings)
    if refusal is not None:
        raise HTTPException(501, detail=refusal)


def require_parallel_key(settings: Settings | None = None) -> None:
    s = settings or default_settings
    if not s.parallel_configured:
        raise HTTPException(501, detail={"feature": "parallel", "env": "PARALLEL_API_KEY=…", "message": "PARALLEL_API_KEY is not configured."})
