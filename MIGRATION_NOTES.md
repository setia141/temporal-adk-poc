# Spike: migrating to the official `temporalio.contrib.google_adk_agents` plugin

This branch replaces the hand-rolled activity-per-agent architecture (on
`master`) with the official plugin: `TemporalModel` + `GoogleAdkPlugin`,
following the pattern from Temporal's own docs. Kept for reference; **not
recommended to merge as-is** — see Verdict below.

## What changed

- `runner/agent_runner.py`: `LiteLlm(model=...)` → `TemporalModel(model_name, activity_config=...)`.
  `InMemoryRunner` replaces the manual `Runner` + `InMemorySessionService`.
- `agents/<name>/activity.py` (an `@activity.defn`) → `agents/<name>/step.py`
  (a plain `async def`, awaited directly from workflow code). The LLM call
  itself is now the thing that becomes a Temporal Activity
  (`invoke_model`, injected automatically by `GoogleAdkPlugin`) — there's no
  need for us to wrap each agent turn in our own Activity anymore.
- `agents/intake/attachment_activity.py` (new): fetching + parsing an
  attachment is still genuine I/O unrelated to any LLM call, so it stays a
  real hand-written `@activity.defn` — the one activity this project still
  defines itself.
- `workflow/intake_workflow.py`: calls `run_intake`/`run_risk_scoring`/etc.
  directly (`await run_risk_scoring(...)`), instead of
  `workflow.execute_activity(risk_scoring_activity, ...)`. Still wraps our
  own module imports in `workflow.unsafe.imports_passed_through()` (that's
  unrelated to the plugin — `google.adk`/`google.genai`/`mcp` get their own
  passthrough from `GoogleAdkPlugin` itself).
- `worker.py` / `clients/*.py`: `Client.connect(..., plugins=[GoogleAdkPlugin()])`.
  `worker.py`'s own `activities=[...]` list shrank from 5 to 1
  (`load_attachment_activity`) — `GoogleAdkPlugin` auto-injects `invoke_model`.
- `requirements.txt`: added `mcp` (see blocker #1 below).

## What actually works (verified against a live `temporal server start-dev`)

- **Custom LLM gateway compatibility, confirmed**: `TemporalModel("openai/gpt-4o-mini")`
  resolves via ADK's `LLMRegistry.new_llm(...)` to a `LiteLlm` instance —
  confirmed directly in this venv (see chat history). `OPENAI_API_BASE`/
  `OPENAI_API_KEY` (or an org gateway) work exactly the same as on `master`,
  since the actual HTTP call is still `litellm`, just invoked from inside
  Temporal's own `invoke_model` Activity instead of one we wrote.
- **Plain dataclasses round-trip fine** through `GoogleAdkPlugin`'s Pydantic-based
  payload converter, including `bytes` fields (`Attachment.image_bytes`) —
  no need to convert our `shared/types.py` dataclasses to pydantic models.
- **Full pipeline ran to completion** end-to-end: intake clarification →
  parallel risk/complexity (genuinely concurrent via `asyncio.gather`, same
  as before) → triage's scripted urgency question → architecture evaluator's
  own clarification → final `IntakeResult`. All 5 agents, both attachment
  and non-attachment paths, clarify/amend machinery — all intact.

## Blockers found

1. **The plugin doesn't import out of the box.** `temporalio.contrib.google_adk_agents/__init__.py`
   unconditionally imports its `_mcp` submodule, which needs `google.adk.tools.mcp_tool.McpToolset`
   — not available unless the `mcp` package is installed (it's not a
   `google-adk` dependency by default; `google.adk.tools.mcp_tool` exports
   nothing without it). Fixed by adding `mcp` to `requirements.txt`, but this
   is a real, currently-undocumented gap between `temporalio[google-adk]` and
   a plain `pip install google-adk`.

2. **A real workflow-task deadlock on (at least) the first LLM call, and it's worse than a simple retry.**
   Temporal's SDK has a hardcoded 2-second "did the workflow coroutine yield"
   deadlock detector (`temporalio/worker/_workflow.py`: `_deadlock_timeout_seconds = None if debug_mode else 2`
   — no tunable threshold, only fully on or fully off via `Worker(debug_mode=True)`).
   ADK's `Runner`/session-construction path does enough synchronous work on
   a call that it can exceed 2 seconds before yielding, which trips this
   detector. Observed consequence: this didn't just fail-and-cleanly-retry
   the workflow task — the worker process's own eviction handling got stuck
   (`"Failed running eviction job... this worker may not complete and the
   slot may remain forever used"`), and the only way to recover was to kill
   that worker process and start a fresh one. Setting `debug_mode=True`
   avoided the failure entirely (confirmed the pipeline completes cleanly
   under it) — but that flag disables deadlock detection *entirely*, which
   is meant for interactive debugging, not routine production use. It masks
   a real problem rather than fixing it.

## Verdict

Architecturally sound and the custom-gateway concern that started this
spike is a non-issue — but the deadlock/stuck-worker behavior under normal
(non-debug) settings is a genuine blocker, not a rough edge to shrug off,
and matches the plugin's own "experimental, may change" warnings. Given
`master`'s hand-rolled activity-per-agent approach has none of this risk
(a slow ADK call inside an Activity is just a slow Activity — Temporal's
2-second deadlock rule only applies to *workflow* code, never Activities),
**stay on `master`'s architecture** for now. Revisit this branch once the
plugin's maturity/documentation catches up, or if a future `temporalio`/
`google-adk` release resolves the deadlock without needing `debug_mode`.
