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
   Root cause identified: ADK's flow code (`google/adk/flows/llm_flows/contents.py`)
   unconditionally imports `google.adk.labs.openai`, which imports the full
   `openai` SDK — and `GoogleAdkPlugin` only passes `google.adk`/`google.genai`/`mcp`
   through the sandbox, so `openai` (and `litellm`, the actual HTTP layer) got
   freshly re-imported inside the sandbox on the first call, and that import alone
   exceeded the 2-second budget. **Fixed** by passing `openai`/`litellm` through
   the sandbox ourselves (`worker.py`'s `workflow_runner=SandboxedWorkflowRunner(...)`)
   — `GoogleAdkPlugin`'s own passthrough additions layer on top of this instead of
   replacing it. No `debug_mode` needed; deadlock detection stays fully on.

3. **No way to pass custom LiteLlm constructor kwargs (api_base, extra_headers)
   through `TemporalModel`.** `invoke_model` resolves the model fresh from just the
   model-name string every call (`LLMRegistry.new_llm(llm_request.model)` →
   `cls(model=model)`, no other kwargs) — there is no per-call path to inject
   `api_base`/`extra_headers`, unlike constructing `LiteLlm(...)` directly.
   Resolved via `runner/gateway_litellm.py`: a `LiteLlm` subclass with
   `AI_GATEWAY_BASE_URL`/`AI_GATEWAY_HEADERS` baked into its constructor,
   re-registered over litellm's exact regex keys in `LLMRegistry` at worker
   startup, so name-only resolution still yields a fully configured client.
   On `master`, the same thing is a single kwarg on `LiteLlm(...)` at the call
   site — no registry manipulation needed.

4. **Plain ADK `FunctionTool`s are unsafe out of the box.** The plugin proxies
   only the model call (and, optionally, MCP toolsets) to activities; a normal
   `LlmAgent(tools=[fn])` function executes inline in workflow code, which is a
   determinism violation the moment it does real I/O. Resolved with the
   activity + wrapper pattern in `agents/<name>/tools.py`: the agent calls a
   same-signature workflow-side wrapper that immediately dispatches the real
   work to its own `@activity.defn`. On `master`, tools just run inside the
   agent's activity with no extra ceremony.

## Verdict

All four blockers now have working resolutions on this branch, and the full
feature set from `master` (agent tools, conversational clarifications with
follow-up handling, multi-attachment support, the intake confirmation
checkpoint, mid-run amendments, call logging) has been ported on top of the
plugin architecture. The honest trade-off is unchanged in kind, just smaller
in degree: the plugin saves hand-written per-agent activities and gives
per-LLM-call visibility/retries for free, but everything that crosses its
name-string model resolution (gateway config) or its workflow-code execution
model (tools) needs an indirection (`LLMRegistry` override, activity-wrapper
pattern) that `master` simply doesn't need. Both branches are now
feature-equivalent and runnable; the plugin remains experimental per its own
docstrings — pick per deployment based on whether per-call activity
granularity is worth the indirections.
