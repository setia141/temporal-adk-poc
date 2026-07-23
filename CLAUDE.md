# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A POC showing Temporal orchestrating multiple Google ADK agents (via `LiteLlm` → OpenAI) to triage an API onboarding intake request. The pipeline is conversational: any interruptible agent can pause mid-stage to ask the user a clarifying question, then resume once answered, rather than always running unattended start-to-finish.

## Commands

```
pip install -r requirements.txt      # install deps (litellm pinned to 1.74.15 — see below)
cp .env.example .env                 # then set OPENAI_API_KEY

temporal server start-dev            # terminal 1 — Temporal dev server + Web UI at localhost:8233
python worker.py                     # terminal 2 — long-lived worker polling adk-agents-task-queue
python clients/starter.py            # terminal 3 — CLI: fill intake form, answer clarifications, see result
streamlit run clients/app.py         # terminal 3 alt — same flow as a chat-style web UI
```

There is no test suite, lint config, or build step in this repo.

`litellm` is pinned to `1.74.15` because newer releases bundle a Rust extension that needs the MSVC linker to build from source on Windows — keep the pin unless Build Tools for Visual Studio ("Desktop development with C++") is installed.

Debugging a stuck workflow: `temporal task-queue describe --task-queue adk-agents-task-queue` to check for a live poller; if the queue has a poller but no progress, `worker.py` is likely wedged (common after a code change or a crashed `run_agent` call) — kill it and restart `python worker.py`. The pending workflow task resumes automatically; no need to restart the workflow itself.

## Architecture

```
clients/starter.py / clients/app.py --> Temporal Server --> worker.py
                                                |
                                IntakeWorkflow (workflow/intake_workflow.py)
                                                |
                     agents.intake.activity ("intake_preparation" agent)
                                                |
                          +-----------+-----------+
                          |                       |
        agents.risk_scoring.activity   agents.complexity_assessment.activity
                (dispatched together via asyncio.gather — genuinely concurrent)
                          |                       |
                          +-----------+-----------+
                                      |
                agents.triage_classification.activity
                                      |
                agents.architecture_evaluator.activity
                                      |
                              IntakeResult
```

### Layout

Each of the five agents is its own component: a folder under `agents/` holding its `prompt.py` (the instruction text, nothing else), `tools.py` (that agent's tool functions, if any), and `activity.py` (the Temporal `@activity.defn` that builds the request and calls `run_agent(...)`). This is deliberate: prompt wording, tool implementations, and orchestration/plumbing code never share a file, so tuning an agent's instructions never touches logic and vice versa.

- `shared/types.py` — dataclasses shared across process boundaries: `IntakeForm`, `AgentRequest`, `AgentResponse`, `IntakeResult`, `TranscriptEntry`, `IntakeStatus`. Re-exported from `shared/__init__.py`.
- `agents/<name>/prompt.py` — the instruction text for that agent, and nothing else.
- `agents/<name>/tools.py` — plain `async def` functions the agent can call mid-turn (e.g. `agents/architecture_evaluator/tools.py` holds `fetch_architecture_standards` and the `ARCHITECTURE_STANDARDS` text it returns). Passed to `run_agent(..., tools=[...])`; ADK wraps each in a `FunctionTool` and drives any tool-calling loop itself inside `runner.run_async`. Since `run_agent` only ever runs inside an `@activity.defn`, a tool doing real I/O is exactly as safe as the LLM call itself — no Temporal determinism/sandbox concerns, unlike a tool that ran directly in workflow code.
- `agents/<name>/activity.py` — the Temporal activity: builds the prompt from the request, calls `run_agent(...)`. This is the only place real I/O (OpenAI calls, tool calls) happens for that agent.
- `agents/intake/attachment.py` — parses a supporting attachment (PDF/image/text) into text or image bytes for the intake agent only; no other agent touches attachments.
- `runner/agent_runner.py` — `run_agent()` builds a fresh ADK `LlmAgent` + `LiteLlm(model=ADK_MODEL)` + `Runner` per call (stateless, safe to retry), runs one turn, and parses the `CLARIFY_NEEDED:` convention out of the response.
- `runner/clarify_prompt.py` — the `CLARIFY_CONVENTION` prompt text and `CLARIFY_PREFIX` marker, appended to every clarification-capable agent's instruction by `run_agent`.
- `storage/attachment_store.py` — pluggable attachment storage (`AttachmentStore` interface, `inline`/`azure_blob` backends), selected via `get_attachment_store()`.
- `workflow/intake_workflow.py` — `IntakeWorkflow`, the only place orchestration logic lives. Re-exported from `workflow/__init__.py`.
- `worker.py` — registers the workflow + all five activities and polls the task queue. Stays at the repo root (the one long-lived process, not a per-invocation client).
- `clients/starter.py` / `clients/app.py` — two independent clients driving the same workflow (CLI vs. Streamlit chat UI); neither talks to the worker directly, only to the Temporal server.
- `test_data/` — sample PDF/image/markdown attachment fixtures for manually exercising the attachment feature; see `test_data/README.md`.

Everything under `agents/`, `shared/`, `storage/`, `runner/`, `workflow/` is imported as a top-level package (e.g. `from agents.intake import intake_activity`, `from shared import IntakeForm`). Because `clients/starter.py` and `clients/app.py` live one level down from those packages, each inserts the repo root at the front of `sys.path` before its other imports — see the top of either file — so `python clients/starter.py` / `streamlit run clients/app.py` work from the repo root regardless of Python's default script-directory sys.path behavior.

### Determinism boundary (critical when touching workflow/intake_workflow.py)

Temporal replays workflow code from history to rebuild state (e.g. after a worker restart), so `IntakeWorkflow.run` must stay deterministic: no network calls, no randomness, no real timers, no direct ADK/LiteLLM usage. It may only call `workflow.execute_activity(...)` (individually or wrapped in `asyncio.gather` for real concurrency — the gather itself is deterministic since the actual I/O is inside the activities). All non-deterministic work (the OpenAI calls, attachment parsing) belongs under `agents/`, never in `workflow/intake_workflow.py`. Activity imports (`from agents... import ...`, `from shared import ...`) are wrapped in `workflow.unsafe.imports_passed_through()` at the top of `intake_workflow.py` for this reason.

### Clarification / human-in-the-loop mechanism

- Every agent instruction (except the two parallel activities) gets `CLARIFY_CONVENTION` appended (`runner/clarify_prompt.py`, applied in `runner/agent_runner.py`): if required input is missing, vague, or a placeholder, the agent must respond with exactly `CLARIFY_NEEDED: <question>`. `run_agent` also has fallback heuristics (scans every line, treats a short single-line "?"-ending response as a question) since some models don't follow the prefix convention exactly.
- `risk_scoring_activity` and `complexity_assessment_activity` run in parallel via `asyncio.gather` and pass `allow_clarification=False` — they're told to state assumptions instead of asking. This is deliberate: the workflow's pending-question state (`_pending_question` / `_latest_answer`) only tracks one question at a time, which breaks if two parallel activities could both pause.
- For a pausable stage, `IntakeWorkflow._run_stage` loops: on `needs_clarification=True` it records the question, sets `_pending_question`, and suspends via `workflow.wait_condition(...)` until an answer (or an amendment, see below) arrives.
  - `get_status` (`@workflow.query`) — read-only snapshot polled by both `starter.py` and `app.py`.
  - `submit_answer` (`@workflow.update`) — carries the user's answer in; its `@submit_answer.validator` synchronously rejects blank answers or answers with no pending question, before they're admitted to history. Update+Query were chosen over Signals specifically for this synchronous accept/reject.
- `_run_stage` has no cap on re-asking: a stage can loop through clarification rounds indefinitely until the agent stops setting `needs_clarification`. There is no forced-final-answer fallback, so a model that keeps finding something to ask about will keep the workflow paused rather than being forced to a conclusion.
- Adding a new pausable stage means routing it through `_run_stage` (not calling `workflow.execute_activity` directly) so it gets this same pause/resume/amendment behavior for free.

### Amending earlier-stage info mid-run

A user can correct a field they already submitted even after later stages have run (or while one is paused asking its own question) via `submit_amendment(field, value)` (`@workflow.update`):

- `AMENDABLE_FIELDS` (`workflow/intake_workflow.py`) maps each `IntakeForm` field to the earliest `STAGE_ORDER` stage that depends on it — the intake-form fields all map to `"intake"` (everything downstream is recomputed), `architecture_notes` maps to `"architecture_evaluator"` only (nothing else consumes it).
- `run()` is a checkpoint loop, not a straight line: each pass checks `restart_from` against `STAGE_ORDER` to decide whether to recompute intake/triage or reuse what's already there; architecture evaluation always (re)runs last.
- If an amendment arrives while a stage is paused on its own clarification question, `_run_stage`'s `wait_condition` wakes on it directly and abandons that question (returns `None`) rather than making the user answer something about to become stale; `run()` sees the `None` and restarts immediately from the amended stage. If nothing is paused, the amendment is picked up at the next natural stage boundary instead.
- Only fields in `AMENDABLE_FIELDS` are accepted; validated in `_validate_submit_amendment`.
- `starter.py` exposes this as an `amend <field>=<value>` command typeable at any time (via a background stdin reader, `_read_line`); `app.py` exposes it as a "Correct earlier info" expander form.

### Supporting attachments (PDF / image / text)

`IntakeForm.attachment_ref` + `attachment_filename` carry an optional supporting file into the intake stage only:

- The client (`starter.py`/`app.py`) reads the file once and calls `get_attachment_store().put(bytes, filename)` (`storage/attachment_store.py`), getting back an opaque `ref` string — never a filesystem path, since the workflow may run on a different host than the client, and never raw bytes in `IntakeForm` directly, to keep workflow-history payloads small.
- `agents/intake/activity.py` calls `get_attachment_store().get(ref)` to fetch the bytes, then `agents/intake/attachment.py`'s `load_attachment()` dispatches by file extension: `.pdf` → `pypdf` text extraction, text-like extensions (`.txt`/`.md`/`.csv`/`.json`/`.yaml`/`.yml`/`.log`) → decoded as UTF-8, image MIME types → kept as raw bytes and passed to `run_agent(..., image_bytes=..., image_mime_type=...)`, which adds a real `types.Part.from_bytes(...)` image part so the vision-capable model actually sees the image.
- Storage backend is chosen by `ATTACHMENT_STORE` env var: `inline` (default, no infra — the ref *is* the base64 payload, riding through Temporal's own payloads; bounded by Temporal's payload/history size limits, fine for typical KB-to-few-MB files) or `azure_blob` (persists to Azure Blob Storage via `AZURE_STORAGE_CONNECTION_STRING`; ref is just a blob name, no size ceiling).

### Streamlit async bridge (app.py)

Streamlit reruns execute inside their own already-running asyncio loop, so plain `asyncio.run()` fails. `app.py` keeps one persistent background event loop alive in a dedicated thread for the whole server process (`_get_background_loop()`, memoized via `st.cache_resource` so it's a true singleton) and dispatches every async Temporal call onto it via `asyncio.run_coroutine_threadsafe(...).result(timeout=...)` (`run_async()`). Two rules this depends on:
- `get_temporal_client()` and any `st.session_state` access must only happen on Streamlit's own thread, never inside a coroutine running on the background loop.
- Every async function in `app.py` takes the resolved `client` (and session values) as plain parameters instead of calling `get_temporal_client()` or reading `st.session_state` itself.

`app.py` logs to `app_debug.log` (gitignored, INFO level) — useful for diagnosing a live server without browser DevTools.

### Other notes

- Model is set via `ADK_MODEL` in `.env` (default `openai/gpt-4o-mini`); route through a LiteLLM gateway/proxy instead of OpenAI directly by setting `OPENAI_API_BASE` to an OpenAI-compatible `/v1` endpoint.
- Whether an agent asks a clarifying question is a genuine LLM judgment call guided by instructions, not a hardcoded trigger — not 100% deterministic, though default form values in `starter.py`/`app.py` are tuned to reliably trigger all three interruptible agents in practice.
