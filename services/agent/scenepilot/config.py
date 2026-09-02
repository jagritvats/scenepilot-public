"""Runtime configuration for the ScenePilot agent service.

Everything is environment-driven so the same code runs locally (SQLite, API keys)
and on Cloud Run (Postgres, Vertex AI / API key).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent.parent

# Load repo-root .env first (shared), then service-local .env (overrides).
load_dotenv(REPO_ROOT / ".env")
load_dotenv(SERVICE_ROOT / ".env", override=True)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _origins(value: str | None) -> tuple[str, ...]:
    return tuple(o.strip().rstrip("/") for o in (value or "").split(",") if o.strip())


@dataclass(frozen=True)
class Settings:
    gemini_model: str
    google_api_key: str | None
    use_vertex: bool
    parallel_api_key: str | None
    parallel_search_mode: str
    parallel_max_chars_total: int | None  # None = let Parallel size excerpts for client_model
    mode: str  # "live" | "replay"
    record: bool
    fallback_to_recording: bool  # live call failed → serve a matching recording, labelled replayed
    database_url: str
    data_dir: Path
    recordings_dir: Path
    port: int
    public_base_url: str | None  # where Parallel can reach our webhook (hosted URL); None = monitors cannot be created live
    # Replay the bundled dossier recordings and the hero screenplay into a freshly-seeded project, so
    # a cold instance (or a new Cloud Run revision on ephemeral SQLite) does not open on empty panels.
    # Nothing is invented — see `seed/warm.py` — and nothing is accepted on the producer's behalf.
    warm_demo: bool = True

    # ----- public exposure -----
    # The hosted demo sits on a public page with no auth, so the service is closed by default: no
    # cross-origin caller, no schema to read. Browser traffic is same-origin through the Next proxy,
    # which sends no Origin header and therefore needs no entry here.
    dev_mode: bool = False  # SCENEPILOT_DEV=1 — serves /docs, /redoc and /openapi.json
    allowed_origins: tuple[str, ...] = ()  # SCENEPILOT_ALLOWED_ORIGINS=https://a,https://b

    # ----- spend containment -----
    # A cap the priced endpoints actually observe (see `services/budget.py`). `parallel_task_max_runs`
    # bounds one request; these bound the deployment.
    paid_call_budget: int = 40  # priced calls per window; <= 0 = uncapped
    paid_call_window_s: int = 86_400
    paid_call_cooldown_s: int = 60  # per endpoint + subject; <= 0 = no cooldown

    # ----- deep-Parallel features -----
    # Invariant: no Parallel call that costs more than a Search, or takes longer than a few seconds,
    # may fire implicitly. Task / FindAll / Memory are OFF by default, are reached only from an
    # explicit UI action, and show their estimated cost and latency before the click.
    parallel_memory_enabled: bool = False
    parallel_task_enabled: bool = False
    parallel_findall_enabled: bool = False
    parallel_memory_scope_prefix: str = "scenepilot"
    parallel_task_processor: str = "core-fast"
    parallel_task_timeout_s: int = 600
    parallel_task_max_runs: int = 4
    parallel_findall_mode: str = "entity_search"  # entity_search (sync) | findall (async, deep)
    parallel_findall_generator: str = "base"
    parallel_findall_match_limit: int = 8
    parallel_findall_timeout_s: int = 300
    parallel_findall_enrich: bool = True  # deep FindAll only: fetch phone/address/day rate for matches

    # ----- irreversible production writes -----
    # Not a spend gate: these cost nothing and call nobody. They are the two writes a producer cannot
    # take back from the UI — a wrapped day is a record, and a committed board replaces the engine's
    # own schedule with a human's typed times.
    #
    # Deliberately the inverse of the Parallel flags above, for the inverse reason. Those are off in
    # code and on in `deploy/cloudrun.sh` because ON costs money. These are on in code and off in
    # `cloudrun.sh` because ON, on a public page with no auth, costs the next visitor the demo. A
    # local clone owns its own database and is the product; the deployment is what closes them.
    allow_wrap: bool = True
    allow_commit_board: bool = False  # built, and off until a deployment says otherwise

    @property
    def gemini_configured(self) -> bool:
        return bool(self.google_api_key) or self.use_vertex

    @property
    def parallel_features(self) -> frozenset[str]:
        """Deep-Parallel features enabled in this environment (see `require_feature`)."""
        on = {
            "memory": self.parallel_memory_enabled,
            "task": self.parallel_task_enabled,
            "findall": self.parallel_findall_enabled,
        }
        return frozenset(name for name, enabled in on.items() if enabled)

    @property
    def write_capabilities(self) -> frozenset[str]:
        """Irreversible production writes this deployment allows (see `require_capability`)."""
        on = {"wrap": self.allow_wrap, "commit_board": self.allow_commit_board}
        return frozenset(name for name, enabled in on.items() if enabled)

    @property
    def parallel_configured(self) -> bool:
        return bool(self.parallel_api_key)

    @property
    def live(self) -> bool:
        return self.mode == "live"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("SCENEPILOT_DATA_DIR") or (SERVICE_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    recordings_dir = Path(
        os.getenv("SCENEPILOT_RECORDINGS_DIR")
        or (SERVICE_ROOT / "scenepilot" / "seed" / "fixtures" / "recordings")
    )
    recordings_dir.mkdir(parents=True, exist_ok=True)

    mode = (os.getenv("SCENEPILOT_MODE") or "live").strip().lower()
    if mode not in {"live", "replay"}:
        mode = "live"

    database_url = os.getenv("DATABASE_URL") or f"sqlite:///{(data_dir / 'scenepilot.db').as_posix()}"

    return Settings(
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-3.5-flash",
        google_api_key=os.getenv("GOOGLE_API_KEY") or None,
        use_vertex=_bool(os.getenv("GOOGLE_GENAI_USE_VERTEXAI"), False),
        parallel_api_key=os.getenv("PARALLEL_API_KEY") or None,
        parallel_search_mode=(os.getenv("PARALLEL_SEARCH_MODE") or "advanced").strip().lower(),
        parallel_max_chars_total=int(os.getenv("PARALLEL_MAX_CHARS_TOTAL")) if (os.getenv("PARALLEL_MAX_CHARS_TOTAL") or "").strip() else None,
        mode=mode,
        record=_bool(os.getenv("SCENEPILOT_RECORD"), False),
        fallback_to_recording=_bool(os.getenv("SCENEPILOT_FALLBACK_TO_RECORDING"), True),
        database_url=database_url,
        data_dir=data_dir,
        recordings_dir=recordings_dir,
        port=int(os.getenv("AGENT_PORT") or "8000"),
        public_base_url=(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/") or None,
        warm_demo=_bool(os.getenv("SCENEPILOT_WARM_DEMO"), True),
        dev_mode=_bool(os.getenv("SCENEPILOT_DEV"), False),
        allowed_origins=_origins(os.getenv("SCENEPILOT_ALLOWED_ORIGINS")),
        paid_call_budget=int(os.getenv("SCENEPILOT_PAID_CALL_BUDGET") or "40"),
        paid_call_window_s=int(os.getenv("SCENEPILOT_PAID_CALL_WINDOW_S") or "86400"),
        paid_call_cooldown_s=int(os.getenv("SCENEPILOT_PAID_CALL_COOLDOWN_S") or "60"),
        parallel_memory_enabled=_bool(os.getenv("SCENEPILOT_PARALLEL_MEMORY"), False),
        parallel_task_enabled=_bool(os.getenv("SCENEPILOT_PARALLEL_TASK"), False),
        parallel_findall_enabled=_bool(os.getenv("SCENEPILOT_PARALLEL_FINDALL"), False),
        parallel_memory_scope_prefix=(os.getenv("PARALLEL_MEMORY_SCOPE_PREFIX") or "scenepilot").strip(),
        parallel_task_processor=(os.getenv("PARALLEL_TASK_PROCESSOR") or "core-fast").strip(),
        parallel_task_timeout_s=int(os.getenv("PARALLEL_TASK_TIMEOUT_S") or "600"),
        parallel_task_max_runs=int(os.getenv("PARALLEL_TASK_MAX_RUNS") or "4"),
        parallel_findall_mode=(os.getenv("PARALLEL_FINDALL_MODE") or "entity_search").strip().lower(),
        parallel_findall_generator=(os.getenv("PARALLEL_FINDALL_GENERATOR") or "base").strip().lower(),
        parallel_findall_match_limit=int(os.getenv("PARALLEL_FINDALL_MATCH_LIMIT") or "8"),
        parallel_findall_timeout_s=int(os.getenv("PARALLEL_FINDALL_TIMEOUT_S") or "300"),
        parallel_findall_enrich=_bool(os.getenv("PARALLEL_FINDALL_ENRICH"), True),
        allow_wrap=_bool(os.getenv("SCENEPILOT_ALLOW_WRAP"), True),
        allow_commit_board=_bool(os.getenv("SCENEPILOT_ALLOW_COMMIT_BOARD"), False),
    )


settings = load_settings()
