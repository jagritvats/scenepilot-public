"""Google ADK runtime: run a Gemini LlmAgent and return a validated structured output.

One `LlmAgent` per production role (scene breakdown, research planner, evidence analyst,
production planner, disruption verifier, rescue planner/explainer). Every invocation is
observable (activity events), validated (pydantic), and optionally recorded for replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings, settings as default_settings
from ..tools.normalize import denormalize, id_order, normalize, rq_run_id
from ..tools.recorder import Recorder, ReplayMiss
from . import prompts

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
APP_NAME = "scenepilot"


class AgentError(RuntimeError):
    pass


class ToolLoopError(AgentError):
    """The model kept calling tools past the hard limit; the invocation was abandoned."""


MAX_TOOL_CALLS_PER_INVOCATION = 6


class GeminiRuntime:
    def __init__(self, on_event: Callable[[str, str, dict], None] | None = None, recorder: Recorder | None = None, settings: Settings | None = None, prompt_version: str = prompts.DEFAULT_VERSION):
        self.settings = settings or default_settings
        self.on_event = on_event or (lambda kind, msg, meta: None)
        self.recorder = recorder or Recorder(self.settings.recordings_dir, self.settings.mode, self.settings.record)
        self.prompt_version = prompt_version
        self.calls = 0
        self.fallbacks = 0

    # ------------------------------------------------------------------ #
    async def run_structured(
        self,
        role: str,
        user_text: str,
        schema: type[T],
        *,
        tools: list[Any] | None = None,
        temperature: float = 0.2,
        max_attempts: int = 2,
        replay_tool_calls: Callable[[list[dict]], None] | None = None,
    ) -> T:
        """Run the `role` agent on `user_text`, return a validated `schema` instance."""
        instruction = prompts.load(role, self.prompt_version)
        order = id_order(user_text)
        key = Recorder.key("gemini", {"role": role, "prompt_version": self.prompt_version, "instruction": instruction, "user_text": normalize(user_text, order), "schema": schema.__name__})
        self.calls += 1
        self.on_event("gemini", f"Gemini · {role.replace('_', ' ')} ({self.settings.gemini_model})", {"role": role, "prompt_version": self.prompt_version, "replay": self.recorder.replay})

        def from_recording(rec: dict) -> T:
            created: list[str] = []
            if replay_tool_calls and rec.get("tool_calls"):
                # recorded args carry placeholders (rq_@@@@@@_1, search_@0): map them to this run's ids first
                calls = json.loads(denormalize(json.dumps(rec["tool_calls"], ensure_ascii=False), order, rq_run_id(user_text)))
                created = list(replay_tool_calls(calls) or [])
            raw = json.dumps(rec["output"], ensure_ascii=False)
            # placeholders → this run's ids (positional: prompt ids, then ids created by tool calls)
            raw = denormalize(raw, order + [c for c in created if c not in order], rq_run_id(user_text))
            return schema.model_validate(json.loads(raw))

        if self.recorder.replay:
            rec = self.recorder.lookup("gemini", key)
            if rec is None:
                raise ReplayMiss(f"No recording for Gemini role '{role}' with this input")
            return from_recording(rec)

        if not self.settings.gemini_configured:
            raise AgentError("Gemini is not configured: set GOOGLE_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=TRUE with ADC)")

        last_error: str | None = None
        text = user_text
        active_tools = list(tools or [])
        for attempt in range(1, max_attempts + 1):
            try:
                output, tool_calls, created_ids = await self._run_once(role, instruction, text, schema, active_tools, temperature)
                if self.recorder.record:
                    # ids in the output (source refs, evidence ids) are stored as placeholders too;
                    # ids created by tool calls during the invocation extend the positional order
                    order_ext = order + [c for c in created_ids if c not in order]
                    norm_out = json.loads(normalize(json.dumps(output.model_dump(mode="json"), ensure_ascii=False), order_ext, dates=False))
                    norm_calls = json.loads(normalize(json.dumps(tool_calls, ensure_ascii=False), order_ext, dates=False))
                    self.recorder.save("gemini", key, {"output": norm_out, "tool_calls": norm_calls}, request={"role": role, "user_text": normalize(user_text, order)})
                return output
            except ValidationError as ve:
                last_error = str(ve)[:1500]
                log.warning("Gemini %s output failed validation (attempt %d): %s", role, attempt, last_error)
                self.on_event("warning", f"Gemini {role}: output failed validation, retrying", {"attempt": attempt})
                text = user_text + f"\n\nYour previous output did not validate against the schema:\n{last_error}\nReturn ONLY valid structured output."
            except ReplayMiss:
                raise
            except ToolLoopError as exc:
                last_error = str(exc)
                log.warning("Gemini %s exceeded the tool-call limit; retrying without tools", role)
                self.on_event("warning", f"Gemini {role} exceeded {MAX_TOOL_CALLS_PER_INVOCATION} tool calls — retrying without tools", {"attempt": attempt})
                active_tools = []
                text = user_text + "\n\nNote: no further searches are available. Grade using ONLY the results above."
                if attempt >= max_attempts:
                    break
                continue
            except Exception as exc:  # noqa: BLE001 — surface as AgentError
                last_error = f"{type(exc).__name__}: {exc}"
                log.exception("Gemini %s failed (attempt %d)", role, attempt)
                if attempt >= max_attempts:
                    break
                await asyncio.sleep(1.5)
        # Demo hardening: a live failure must not dead-end a demo click. If this exact input was
        # recorded during an earlier real run, serve it — visibly labelled as a replayed recording.
        if self.settings.fallback_to_recording:
            rec = self.recorder.lookup("gemini", key)
            if rec is not None:
                self.on_event("warning", f"Gemini {role} unavailable ({(last_error or '')[:120]}) — served the recorded response from an earlier live run (replayed)", {"role": role, "fallback": True})
                self.fallbacks += 1
                return from_recording(rec)
        raise AgentError(f"Gemini {role} failed: {last_error}")

    # ------------------------------------------------------------------ #
    async def _run_once(self, role: str, instruction: str, user_text: str, schema: type[T], tools: list[Any], temperature: float) -> tuple[T, list[dict], list[str]]:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        agent = LlmAgent(
            name=f"scenepilot_{role}",
            model=self.settings.gemini_model,
            description=f"ScenePilot {role.replace('_', ' ')} agent",
            instruction=instruction,
            output_schema=schema,
            output_key="result",
            tools=list(tools),
            generate_content_config=types.GenerateContentConfig(temperature=temperature),
        )
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
        user_id = "producer"
        session = await session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=f"{role}-{uuid.uuid4().hex[:8]}")
        message = types.Content(role="user", parts=[types.Part(text=user_text)])

        final_text: str | None = None
        tool_calls: list[dict] = []
        created_ids: list[str] = []
        gen = runner.run_async(user_id=user_id, session_id=session.id, new_message=message)
        async for event in gen:
            for fr in event.get_function_responses() or []:
                resp = fr.response if isinstance(fr.response, dict) else {}
                for key_name in ("search_run_id", "extract_run_id"):
                    sid = resp.get(key_name) if isinstance(resp, dict) else None
                    if isinstance(sid, str) and sid:
                        created_ids.append(sid)
            for fc in event.get_function_calls() or []:
                if fc.name == "set_model_response":
                    continue
                args = dict(fc.args or {})
                tool_calls.append({"name": fc.name, "args": args})
                if len(tool_calls) > MAX_TOOL_CALLS_PER_INVOCATION:
                    await gen.aclose()
                    raise ToolLoopError(f"{len(tool_calls)} tool calls in one invocation (limit {MAX_TOOL_CALLS_PER_INVOCATION})")
                self.on_event("gemini", f"Gemini {role} called tool {fc.name}", {"args": {k: (v if isinstance(v, (str, int, float, bool)) else json.loads(json.dumps(v, default=str))) for k, v in args.items()}})
            if event.is_final_response() and event.content and event.content.parts:
                texts = [p.text for p in event.content.parts if getattr(p, "text", None)]
                if texts:
                    final_text = "".join(texts)

        # Prefer session state (ADK stores the structured output under output_key)
        stored = None
        try:
            sess = await session_service.get_session(app_name=APP_NAME, user_id=user_id, session_id=session.id)
            stored = (sess.state or {}).get("result") if sess else None
        except Exception:  # noqa: BLE001
            stored = None
        raw: Any = stored if stored not in (None, "") else final_text
        if raw is None:
            raise ValidationError.from_exception_data("EmptyResponse", [])  # type: ignore[arg-type]
        data = _coerce_json(raw)
        return schema.model_validate(data), tool_calls, created_ids


def _coerce_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, BaseModel):
        return raw.model_dump()
    s = str(raw).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise
