"""A spend cap the priced endpoints actually observe.

The product's rule is that no expensive Parallel call fires implicitly. The rule this adds is that
an explicit one can still be refused, because the hosted demo sits on a public page for weeks with
no auth: a dossier is ~$0.025 a click, a monitor bills every day until it is cancelled, and there is
nothing between a bored visitor and either. `ParallelTaskTool.max_runs` cannot be that guard — the
tool is constructed fresh per request, so its counter starts at zero every time and only ever bounds
a single call.

Two guards, both per process and in memory. The ledger is spend control, not an audit trail: what
was actually researched is the TaskRun / FindAllRun rows, which are persisted.

* a **cooldown** per endpoint + subject, so one button cannot be leaned on; and
* a **cap** on priced calls in a rolling window.

A refusal is shaped like `require_feature`'s, because it is the same statement: a priced capability
this deployment is not spending on right now, named, costed, and with the setting that reopens it.

Both guards bound *spend*, so neither may fire where there is none. Outside `SCENEPILOT_MODE=live`
every recorded endpoint is answered from a committed recording: nothing leaves the process and the
meter does not move, so nothing is booked and nothing is refused. A cooldown that told a judge
re-running the rain fixture that it "would ask the same question again at full price" would be
stating a price that was never charged — and blocking the run-it-again path for no saving at all.
The exemption is per endpoint, not per mode: a Parallel Monitor is a stateful object created on
Parallel's side that keeps billing until cancelled, so it is never recorded and never exempt.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..config import Settings, settings as default_settings


@dataclass(frozen=True)
class PricedCall:
    label: str
    cost: str
    # Served from a committed recording outside `SCENEPILOT_MODE=live`, and therefore free there.
    # Conservatively false: an endpoint nobody has classified is assumed to spend real money.
    recorded: bool = False


PRICED: dict[str, PricedCall] = {
    "dossier": PricedCall("A location dossier", "Parallel Task API — ~$0.025 per location dossier, 1–5 min", recorded=True),
    "preflight": PricedCall("A pre-flight re-check", "Parallel Task API — ~$0.025 per researched location on the day", recorded=True),
    "weather": PricedCall("An hourly weather timeline", "Parallel Task API — ~$0.025 per shoot-day weather timeline, 1–5 min", recorded=True),
    "substitutes": PricedCall("A substitute search", "Parallel FindAll / Entity Search — ~$0.005–$0.50 per run", recorded=True),
    # Not recorded, and it cannot be: a monitor is a stateful object created on Parallel's side that
    # bills every day until somebody cancels it. Replay mode does not make one free, so replay mode
    # does not exempt it.
    "monitors": PricedCall("A live monitor", "Parallel Monitor — ~$0.07/day per hourly lite monitor, billed until cancelled"),
    "plan": PricedCall("A scene planning run", "Gemini + Parallel Search — a few cents per run", recorded=True),
    "disruption": PricedCall("A rescue run", "Gemini + Parallel Search — a few cents per run", recorded=True),
}


def _unknown(name: str) -> PricedCall:
    return PricedCall(f"A {name} call", "a priced Parallel call")


def costs_money(name: str, s: Settings) -> bool:
    """Would this call actually spend anything in this deployment's mode?

    Outside live mode a recorded endpoint is answered from a committed recording in the repository:
    no request leaves the process and the meter does not move. Booking it against the budget would be
    a ledger of money nobody spent, and refusing it would tell a judge that running the rain fixture
    a second time costs full price when it costs nothing — a false statement that also blocks the one
    path the demo is most often asked to repeat.
    """
    return s.live or not PRICED.get(name, _unknown(name)).recorded


class CallBudget:
    """Per-process ledger of priced calls: what was spent, when, and on what."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._spent: list[tuple[float, str, int]] = []  # (at, name, units)
        self._last: dict[str, float] = {}  # "name:subject" -> at

    def reset(self) -> None:
        with self._lock:
            self._spent.clear()
            self._last.clear()

    def charge(self, name: str, subject: str, units: int = 1, settings: Settings | None = None) -> dict[str, object] | None:
        """Book `units` priced calls, or return the refusal detail explaining why not.

        A call that spends nothing in this mode is allowed without being booked: there is no ledger
        entry, because there was no spend to record.
        """
        s = settings or default_settings
        if not costs_money(name, s):
            return None
        now = self._clock()
        with self._lock:
            self._prune(now, s)
            key = f"{name}:{subject}"
            last = self._last.get(key)
            if s.paid_call_cooldown_s > 0 and last is not None and now - last < s.paid_call_cooldown_s:
                return _refusal(name, "cooldown", int(s.paid_call_cooldown_s - (now - last)) + 1, self._used(), s)
            if s.paid_call_budget > 0 and self._used() + units > s.paid_call_budget:
                return _refusal(name, "cap", s.paid_call_window_s, self._used(), s)
            self._spent.append((now, name, units))
            self._last[key] = now
        return None

    def state(self, settings: Settings | None = None) -> dict[str, object]:
        s = settings or default_settings
        now = self._clock()
        with self._lock:
            self._prune(now, s)
            used = self._used()
        return {
            "spent": used,
            "budget": s.paid_call_budget,
            "remaining": max(0, s.paid_call_budget - used) if s.paid_call_budget > 0 else None,
            "window_s": s.paid_call_window_s,
            "cooldown_s": s.paid_call_cooldown_s,
            "env": f"SCENEPILOT_PAID_CALL_BUDGET={s.paid_call_budget}",
            # Why the ledger can sit at zero all day: outside live mode the recorded endpoints are
            # answered from the repository and are never booked.
            "mode": s.mode,
            "charged_in_this_mode": sorted(n for n in PRICED if costs_money(n, s)),
        }

    def _prune(self, now: float, s: Settings) -> None:
        if s.paid_call_window_s > 0:
            cutoff = now - s.paid_call_window_s
            self._spent = [e for e in self._spent if e[0] > cutoff]

    def _used(self) -> int:
        return sum(units for _, _, units in self._spent)


def _refusal(name: str, reason: str, retry_after_s: int, used: int, s: Settings) -> dict[str, object]:
    priced = PRICED.get(name) or _unknown(name)
    if reason == "cooldown":
        env = f"SCENEPILOT_PAID_CALL_COOLDOWN_S={s.paid_call_cooldown_s}"
        message = (
            f"{priced.label} for this exact subject ran less than {s.paid_call_cooldown_s}s ago and would ask the "
            f"same question again at full price. It is available again in {retry_after_s}s."
        )
    else:
        env = f"SCENEPILOT_PAID_CALL_BUDGET={s.paid_call_budget}"
        message = (
            f"This deployment has spent its budget of {s.paid_call_budget} priced Parallel calls "
            f"({used} used). {priced.label} is refused until the window rolls or the budget is raised."
        )
    return {
        "feature": name,
        "env": env,
        "cost": priced.cost,
        "message": message,
        "reason": reason,
        "retry_after_s": retry_after_s,
        "spent": used,
        "budget": s.paid_call_budget,
    }


call_budget = CallBudget()
