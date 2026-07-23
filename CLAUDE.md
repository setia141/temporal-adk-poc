# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A POC showing Temporal orchestrating multiple Google ADK agents to triage an API onboarding intake request, built on the official `temporalio.contrib.google_adk_agents` plugin (`TemporalModel` + `GoogleAdkPlugin`). The pipeline is conversational: any interruptible agent can pause mid-stage to ask the user a clarifying question (and answer follow-up questions conversationally), the user must review/confirm the intake summary before downstream stages run, and earlier-stage fields can be amended mid-run.

## Commands

```
pip install -r requirements.txt      # install deps (litellm pinned to 1.74.15 — see below)
cp .env.example .env                 # then set OPENAI_API_KEY (and optionally AI_GATEWAY_* vars)

temporal server start-dev            # terminal 1 — Temporal dev server + Web UI at localhost:8233
python worker.py                     # terminal 2 — long-lived worker polling adk-agents-task-queue
python clients/starter.py            # terminal 3 — CLI client
streamlit run clients/app.py         # terminal 3 alt — chat-style web UI
```

There is no test suite, lint config, or build step in this repo.

`litellm` is pinned to `1.74.15` because newer releases bundle a Rust extension that needs the MSVC linker to build from source on Windows. `mcp` is a required dependency even though nothing here uses MCP — the plugin's `__init__` unconditionally imports its `_mcp` submodule.

Debugging a stuck workflow: `temporal task-queue describe --task-queue adk-agents-task-queue` to check for a live poller; if the queue has a poller but no progress, `worker.py` is likely wedged — kill and restart it; the pending workflow task resumes automatically.

## Architecture (plugin-based — differs from `master`)

On `master`, each agent turn is a hand-written `@activity.defn`. On this branch, agent code (ADK `LlmAgent` + `InMemoryRunner` event loop, response parsing, tool-calling loop) runs **as workflow code**; only the actual LLM HTTP call becomes a Temporal Activity (`invoke_model`, auto-registered by `GoogleAdkPlugin`). `run_intake` / `run_risk_scoring` / etc. are plain `async def`s awaited directly from `IntakeWorkflow.run`, not dispatched via `workflow.execute_activity`.

### Layout

Each of the five agents is a folder under `agents/` holding `prompt.py` (instruction text, nothing else), `tools.py` (that agent's tool: an `@activity.defn` doing the real work — stubbed for now — plus a same-signature workflow-side wrapper the agent actually calls), and `step.py` (builds the prompt, calls `run_agent(...)`).

- `shared/types.py` — dataclasses shared across process boundaries: `IntakeForm` (attachments as parallel `attachment_refs`/`attachment_filenames` lists), `AgentRequest`, `AgentResponse`, `IntakeResult`, `TranscriptEntry`, `IntakeStatus` (incl. `awaiting_confirmation`).
- `agents/<name>/tools.py` — the tool pair. The wrapper runs in workflow code and immediately dispatches to its activity via `workflow.execute_activity`; **never do real I/O directly in a tool wrapper** — that's a determinism violation, since ADK drives the tool-calling loop in workflow code here. The wrapper's docstring is what the LLM sees.
- `agents/intake/attachment_activity.py` — `load_attachments_activity`: fetches + parses every attachment (PDF → `pypdf` text, text-like → UTF-8, images → raw bytes for the vision model) in one genuine Activity.
- `runner/agent_runner.py` — `run_agent()`: builds `LlmAgent` + `TemporalModel` per call, runs one turn, parses the `CLARIFY_NEEDED:` convention. Logs each call/response via `workflow.logger` (replay-safe — a plain module logger would double-log during replay).
- `runner/clarify_prompt.py` — the clarification convention: task-scoped asking only (an agent must not ask about placeholder values in fields its task doesn't depend on), and conversational follow-ups (if the user's reply is itself a question, answer it first, then re-ask).
- `runner/gateway_litellm.py` — custom-gateway support: `invoke_model` rebuilds the LLM from just the model-name string via `LLMRegistry`, so constructor kwargs can't be passed per-call; when `AI_GATEWAY_BASE_URL`/`AI_GATEWAY_HEADERS` are set, this registers a `LiteLlm` subclass (under litellm's exact existing regex keys — identical strings required for replacement) with those baked in. Registered in `worker.py` at startup.
- `workflow/intake_workflow.py` — `IntakeWorkflow`, the only orchestration logic.
- `worker.py` — registers the workflow, `load_attachments_activity`, and the five tool activities (`GoogleAdkPlugin` auto-registers `invoke_model`). Also configures the sandbox passthrough (below).
- `clients/starter.py` / `clients/app.py` — CLI and Streamlit clients; both connect with `plugins=[GoogleAdkPlugin()]`. Both insert the repo root into `sys.path` (they live one level down from the top-level packages).

### Determinism boundary (critical — different from master)

Workflow code here *includes* the ADK agent machinery, so the rules apply to more code than on `master`:

- `IntakeWorkflow.run`, `_run_stage`, every `agents/<name>/step.py`, every tool **wrapper**, and `run_agent` itself all execute as workflow code — no network, no randomness, no real timers, no direct I/O anywhere in them. The plugin patches ADK's time/uuid providers to Temporal's deterministic ones.
- Real I/O lives only in Activities: `invoke_model` (the LLM call), `load_attachments_activity`, and the five `*_activity` functions in `tools.py`.
- `worker.py` passes `openai`/`litellm` through the workflow sandbox (`SandboxedWorkflowRunner` + `with_passthrough_modules`). ADK's flow code imports them from workflow code; on a cold machine the sandboxed re-import can exceed Temporal's hardcoded 2s deadlock budget (`[TMPRL1101]`, observed here — it wedges the worker, not just the task). The plugin's own passthroughs (`google.adk`/`google.genai`/`mcp`) layer on top.

### Clarification / confirmation / amendment flow

- `CLARIFY_CONVENTION` (appended to every interruptible agent's instruction) makes the agent end its response with `CLARIFY_NEEDED: <question>` when *task-relevant* input is missing. `run_agent` extracts the question; `response.output` (the full conversational reply, marker line cleaned) is what's surfaced to the user — not just the bare question — so follow-up answers survive.
- `risk_scoring` and `complexity_assessment` run in parallel via `asyncio.gather` with `allow_clarification=False` (the pending-question state holds one question at a time).
- After intake produces its canonical summary, the workflow sets `awaiting_confirmation` and pauses; the user confirms via the `confirm_intake_summary` update (or amends a field, which re-runs intake and lands back on the checkpoint) before triage starts.
- `submit_answer` / `submit_amendment` are `@workflow.update`s with validators (synchronous pre-history rejection — the reason Updates were chosen over Signals). `get_status` is the `@workflow.query` both clients poll.
- `_run_stage` has no cap on clarification rounds; an amendment arriving while a stage is paused abandons that stage's question and restarts from the amended stage (`AMENDABLE_FIELDS` maps field → earliest dependent stage).

### Attachments

`IntakeForm.attachment_refs`/`attachment_filenames` carry any number of supporting files. Clients call `get_attachment_store().put(...)` per file (`storage/attachment_store.py`, `inline` default / `azure_blob` via `ATTACHMENT_STORE`); the workflow loads them all in one `load_attachments_activity` call; `run_intake` folds every text attachment into the prompt and passes images to `run_agent(images=[...])` as real image parts.

### Custom LLM gateway

`ADK_MODEL` with any litellm provider prefix (`openai/`, `azure/`, `anthropic/`, `gemini/`, ...) routes through litellm; `AI_GATEWAY_BASE_URL` + `AI_GATEWAY_HEADERS` apply to all of them via `runner/gateway_litellm.py`. An Azure-shaped gateway (`.../openai/deployments/{model}`) is better served by `ADK_MODEL=azure/<deployment>` + `AZURE_API_BASE`/`AZURE_API_KEY`/`AZURE_API_VERSION`. See `.env.example`.

### Streamlit async bridge (app.py)

Streamlit reruns execute inside their own asyncio loop, so `app.py` keeps one persistent background loop in a dedicated thread (`_get_background_loop()`, memoized via `st.cache_resource`) and dispatches every async Temporal call via `asyncio.run_coroutine_threadsafe(...).result(timeout=...)`. `get_temporal_client()` and `st.session_state` must only be touched from Streamlit's own thread — every async function takes the resolved client/session values as parameters. `app.py` logs to `app_debug.log` (gitignored).

### Other notes

- Whether an agent asks a clarifying question is a genuine LLM judgment guided by instructions, not a hardcoded trigger; default form values in the clients are tuned to reliably trigger the interruptible agents.
- `MIGRATION_NOTES.md` records the spike history and blockers found (and their resolutions) — useful context for why this branch differs from `master`.
