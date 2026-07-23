# Temporal + Google ADK POC — API Intake Triage (official plugin architecture)

A proof of concept showing Temporal orchestrating multiple Google ADK agents
to triage a new API intake request, built on the official
`temporalio.contrib.google_adk_agents` plugin (`TemporalModel` +
`GoogleAdkPlugin`). The pipeline is conversational: any interruptible agent
can pause mid-stage to ask the user a clarifying question (and answer
follow-up questions like a normal conversation), the user reviews and
confirms the intake summary before downstream stages run, and earlier-stage
fields can be corrected mid-run.

> `master` holds the alternative architecture (a hand-written
> `@activity.defn` per agent, no plugin). `MIGRATION_NOTES.md` records the
> differences, the blockers found migrating to the plugin, and how each was
> resolved.

## Architecture

```
clients/starter.py / clients/app.py --> Temporal Server --> worker.py
                                                |
                                IntakeWorkflow (workflow/intake_workflow.py)
                                                |
                        run_intake  (+ load_attachments_activity,
                                       lookup_requesting_team tool)
                                                |
                              [user confirms intake summary]
                                                |
                          +---------------------+---------------------+
                          |                                           |
                 run_risk_scoring                        run_complexity_assessment
            (lookup_prior_incidents tool)          (lookup_downstream_dependencies tool)
                (dispatched together via asyncio.gather — genuinely concurrent)
                          |                                           |
                          +---------------------+---------------------+
                                                |
                          run_triage_classification
                          (lookup_team_review_capacity tool)
                                                |
                          run_architecture_evaluator
                          (fetch_architecture_standards tool)
                                                |
                                          IntakeResult
```

The key difference from `master`: `run_intake` / `run_risk_scoring` / etc.
are plain `async def`s awaited **directly from workflow code** — the ADK
agent machinery (LlmAgent, runner event loop, tool-calling loop, response
parsing) runs inside the workflow. Only the real I/O becomes Temporal
Activities:

- `invoke_model` — the actual LLM HTTP call, auto-registered by
  `GoogleAdkPlugin`; every model call shows up in Temporal's UI as its own
  activity with independent retries/timeouts (`summary` set to the agent
  name).
- `load_attachments_activity` — fetching + parsing the form's attachments.
- One `*_activity` per agent tool — see "Agent tools" below.

## Project structure

```
shared/
  types.py              Dataclasses shared across process boundaries
                        (IntakeForm, AgentRequest, AgentResponse,
                        IntakeResult, TranscriptEntry, IntakeStatus)
runner/
  agent_runner.py       run_agent() — one ADK agent turn via TemporalModel;
                        parses the CLARIFY_NEEDED: convention; logs each
                        call/response via workflow.logger (replay-safe)
  clarify_prompt.py     The clarification convention appended to every
                        interruptible agent's instruction
  gateway_litellm.py    Custom-gateway support (AI_GATEWAY_BASE_URL /
                        AI_GATEWAY_HEADERS) via an LLMRegistry override
storage/
  attachment_store.py   Pluggable attachment storage: "inline" (default) or
                        "azure_blob", selected via ATTACHMENT_STORE env var
agents/<name>/
  prompt.py             The agent's instruction text, nothing else
  tools.py              The agent's tool: an @activity.defn (real work,
                        stubbed for now) + a same-signature workflow-side
                        wrapper the agent actually calls
  step.py               Builds the prompt, calls run_agent(...)
agents/intake/
  attachment.py         Parses one attachment (PDF/image/text) into text or
                        image bytes
  attachment_activity.py load_attachments_activity — loads every attachment
                        on the form in one genuine Activity
workflow/
  intake_workflow.py    IntakeWorkflow — all orchestration: staged pipeline,
                        clarification pause/resume, intake confirmation
                        checkpoint, mid-run amendments
worker.py               Long-lived worker: registers the workflow, the
                        attachment activity, and the five tool activities;
                        configures the sandbox passthrough and the gateway
                        registry override
clients/
  starter.py            CLI client (multi-attachment prompts, confirmation
                        checkpoint, amend command, clarification answers)
  app.py                Streamlit chat UI (same flow; also a "Recent runs"
                        picker to resume any run from Temporal)
test_data/              Sample PDF/image/markdown attachment fixtures
```

Every package above is imported as a top-level package. `clients/*` insert
the repo root at the front of `sys.path` so they work when run from the
repo root.

## Setup

1. `pip install -r requirements.txt`

   - `litellm` is pinned to `1.74.15`: newer releases bundle a Rust
     extension needing the MSVC linker to build on Windows.
   - `mcp` is required even though nothing here uses MCP — the plugin's
     `__init__` unconditionally imports its `_mcp` submodule.

2. `cp .env.example .env`, then set `OPENAI_API_KEY` (or have it in your
   environment — `python-dotenv` never overrides an existing variable).

   **Custom org gateway**: set `AI_GATEWAY_BASE_URL` and/or
   `AI_GATEWAY_HEADERS` to route every litellm-backed model (`openai/...`,
   `azure/...`, `anthropic/...`, `gemini/...`, ...) through your gateway
   with extra auth/tenant headers. This works via `runner/gateway_litellm.py`:
   the plugin's `invoke_model` activity rebuilds the LLM from just the
   model-name string (`LLMRegistry.new_llm(name)` → `cls(model=name)`), so
   constructor kwargs can't be passed per-call — instead a `LiteLlm`
   subclass with the gateway config baked in is registered over litellm's
   patterns at worker startup. For an Azure-shaped gateway
   (`.../openai/deployments/{model}`), prefer `ADK_MODEL=azure/<deployment>`
   with `AZURE_API_BASE`/`AZURE_API_KEY`/`AZURE_API_VERSION` — litellm
   builds that URL shape natively. See `.env.example` for both.

## Run

Three long-lived processes, three terminals:

```
temporal server start-dev        # 1 — Temporal dev server, Web UI at localhost:8233
python worker.py                 # 2 — worker polling adk-agents-task-queue
python clients/starter.py        # 3 — CLI (or: streamlit run clients/app.py)
```

The CLI prompts for any number of supporting attachments (blank to stop),
then the form fields. After the intake agent produces its canonical
summary, the run pauses for you to review it — press Enter to confirm, or
`amend <field>=<value>` to correct something (which re-runs intake and
brings you back to the checkpoint). Any interruptible stage may pause to
ask a clarifying question; answering with a question of your own gets a
conversational answer plus a re-ask, not a verbatim repeat. The Streamlit
UI exposes the same flow as a chat, with a file-uploader for multiple
attachments, a "Looks good, continue" confirmation button, and a "Correct
earlier info" form.

Default form values are tuned so a first-time run reliably shows the
interruptible agents asking questions — fill in real details to see them
skip straight to answers.

### Troubleshooting: workflow stuck at "Running"

Check `temporal task-queue describe --task-queue adk-agents-task-queue`
for a poller. If a poller is listed but nothing progresses, the worker is
likely wedged — kill it and restart `python worker.py`; the pending
workflow task resumes automatically without restarting the workflow.

## How it works

1. A client starts `IntakeWorkflow` and polls its `get_status` query.
   Temporal's event history — not any process's memory — is the source of
   truth; if the worker dies mid-run (even while paused on a question),
   a new worker resumes exactly where it left off without re-paying for
   completed LLM calls.

2. The workflow runs the staged pipeline shown above. Each stage calls
   `run_agent(...)` (via its `step.py`), which builds an ADK `LlmAgent`
   whose model is `TemporalModel` — so every LLM call is executed inside
   Temporal's `invoke_model` activity with its own retry policy, while the
   surrounding agent logic replays deterministically as workflow code (the
   plugin patches ADK's time/uuid providers to Temporal's deterministic
   ones and passes `google.adk`/`google.genai`/`mcp` through the sandbox).

3. **Agent tools**: each agent has one tool (stubbed pending real
   backends). Because the tool-calling loop runs in workflow code on this
   branch, a tool that did real I/O inline would violate Temporal's
   determinism rules — so each tool is a same-signature wrapper that
   immediately dispatches to its own `@activity.defn`. The LLM sees the
   wrapper's docstring; the activity does the work; replay stays correct.
   Never do real I/O directly in a tool wrapper.

4. **Clarifications**: interruptible agents end their response with
   `CLARIFY_NEEDED: <question>` when task-relevant input is missing
   (`runner/clarify_prompt.py`). The convention is scoped — an agent must
   not ask about placeholder values in fields its task doesn't depend on —
   and conversational: if the user's reply is itself a question, the agent
   answers it first, then re-asks. `_run_stage` records the question,
   pauses on `workflow.wait_condition`, and resumes when `submit_answer`
   (a `@workflow.update` with a synchronous validator) delivers the
   answer. The two parallel stages pass `allow_clarification=False` since
   the pending-question state holds one question at a time.

5. **Confirmation checkpoint**: after intake completes, the workflow sets
   `awaiting_confirmation` and pauses until `confirm_intake_summary` (or
   an amendment) arrives — downstream stages never run against a summary
   the user hasn't seen.

6. **Amendments**: `submit_amendment(field, value)` corrects an
   already-submitted field at any time. `AMENDABLE_FIELDS` maps each field
   to the earliest stage depending on it; everything from that stage on is
   re-run. An amendment arriving while a stage is paused on its own
   question abandons that question rather than making the user answer
   something about to become stale.

7. **Attachments**: the form carries any number of files as parallel
   `attachment_refs`/`attachment_filenames` lists. Clients upload bytes to
   the attachment store and send opaque refs (never paths — the worker may
   be on a different host; never raw bytes — keeps history payloads
   small). The workflow loads them all in one `load_attachments_activity`
   call; text attachments are folded into the intake prompt, images are
   passed as real image parts so a vision-capable model sees them.

### Streamlit internals: running async Temporal calls from a sync script

Streamlit executes script/fragment reruns inside its own running asyncio
loop, so `asyncio.run()` fails there. `app.py` keeps one persistent
background event loop in a dedicated thread (`_get_background_loop()`,
memoized with `st.cache_resource`) and dispatches every async call onto it
via `asyncio.run_coroutine_threadsafe(...).result(timeout=...)`. The
explicit timeout turns silent deadlocks into visible errors. Two rules:
`get_temporal_client()` and `st.session_state` are only touched from
Streamlit's own thread, and every async function takes the resolved
client/session values as plain parameters. `app.py` logs to
`app_debug.log` (gitignored).

## Notes

- Model selection is `ADK_MODEL` in `.env` (default `openai/gpt-4o-mini`);
  any litellm provider prefix works, and the gateway env vars apply to all
  of them uniformly.
- `worker.py` passes `openai`/`litellm` through the workflow sandbox: ADK's
  flow code imports them from workflow code, and on a cold machine that
  sandboxed re-import can exceed Temporal's hardcoded 2-second deadlock
  budget (`[TMPRL1101]` — observed on this machine; it wedges the worker,
  not just the task). Two lines of insurance against a real failure mode.
- `ARCHITECTURE_STANDARDS` (returned by the architecture evaluator's
  `fetch_architecture_standards` tool) is an invented-but-plausible
  baseline — swap the stub for your org's real standards service.
- Whether an agent asks a clarifying question is a genuine LLM judgment
  guided by instructions, not a hardcoded trigger — default form values
  are tuned so the interruptible agents ask reliably in practice.
