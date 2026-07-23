# Temporal + Google ADK (LiteLLM -> OpenAI) POC — API Intake Triage

A minimal proof of concept showing Temporal orchestrating multiple Google ADK
agents to triage a new API intake request, where each agent uses ADK's
`LiteLlm` model wrapper to call OpenAI models (optionally through a LiteLLM
gateway/proxy). The pipeline is conversational: any interruptible agent can
pause mid-stage and ask the user a clarifying question, then resume once
answered, instead of always running start-to-finish unattended.

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
                (runs in parallel via asyncio.gather)
                          |                       |
                          +-----------+-----------+
                                      |
                agents.triage_classification.activity
                            ("triage_classification" agent)
                                      |
                agents.architecture_evaluator.activity
                          ("architecture_evaluator" agent)
                                      |
                              IntakeResult
```

Each activity runs as its own Temporal Activity, so it gets independent
retries, timeouts, and visibility in Temporal's UI/history. Risk scoring and
complexity assessment are dispatched together and genuinely run
concurrently (not just concurrently-scheduled) — see "How It Works" below.

## Project Structure

Each of the five agents is its own component: a folder under `agents/`
holding just its `prompt.py` (instruction text, nothing else) and
`activity.py` (the Temporal activity that builds the request and calls the
shared agent runner). Prompt wording and orchestration code never share a
file.

```
shared/
  types.py             Dataclasses shared between workflow and activities
                        (IntakeForm, AgentRequest, AgentResponse,
                        IntakeResult, TranscriptEntry, IntakeStatus)
runner/
  agent_runner.py       run_agent() — builds and runs a single ADK LlmAgent
                        turn via LiteLlm; detects the CLARIFY_NEEDED:
                        convention
  clarify_prompt.py     CLARIFY_CONVENTION prompt text + CLARIFY_PREFIX
                        marker, shared by every clarification-capable agent
storage/
  attachment_store.py   Pluggable attachment storage: "inline" (bytes ride
                        through Temporal itself) or "azure_blob" backend,
                        selected via ATTACHMENT_STORE env var
agents/
  intake/               prompt.py, activity.py (intake_activity), plus
                        attachment.py (parses a supporting PDF/image/text
                        file — the only agent that takes an attachment)
  risk_scoring/          prompt.py, activity.py (risk_scoring_activity)
  complexity_assessment/ prompt.py, activity.py (complexity_assessment_activity)
  triage_classification/ prompt.py, activity.py (triage_classification_activity)
  architecture_evaluator/ prompt.py (also holds ARCHITECTURE_STANDARDS),
                        activity.py (architecture_evaluator_activity)
workflow/
  intake_workflow.py    IntakeWorkflow — orchestrates the five activities
                        (two of them in parallel), pausing for clarification
                        via an update + query, and supporting mid-run
                        amendment of earlier-stage info
worker.py                Long-lived process that polls Temporal and executes
                         workflow/activity tasks. Stays at the repo root —
                         the one long-lived process, not a per-invocation
                         client.
clients/
  starter.py              CLI entry point: prompts for the intake form
                         fields, starts one workflow run, prompts for
                         answers when an agent asks a clarifying question,
                         prints the result
  app.py                  Streamlit UI: a form to submit an intake request,
                         the same start/poll/answer flow as starter.py
                         rendered as a chat, plus a picker to resume a run
                         left mid-conversation
test_data/               Sample PDF/image/markdown attachment fixtures for
                         manually testing the attachment feature
requirements.txt         Python dependencies
.env.example             Template for local environment/config
```

Every package above (`shared`, `runner`, `storage`, `agents`, `workflow`) is
imported as a top-level package — e.g. `from agents.intake import
intake_activity`, `from shared import IntakeForm`. Because `clients/starter.py`
and `clients/app.py` live one level below those packages, each inserts the
repo root at the front of `sys.path` before its other imports (see the top of
either file), so `python clients/starter.py` / `streamlit run clients/app.py`
resolve those imports correctly when run from the repo root.

## Setup

1. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

   Note: `litellm` is pinned to `1.74.15` in `requirements.txt`. Newer
   litellm releases bundle a Rust extension (`litellm-rust`) that requires
   the MSVC linker (`link.exe`) to build from source on Windows. Either keep
   the pin, or install "Desktop development with C++" via the [Build Tools
   for Visual Studio](https://visualstudio.microsoft.com/downloads/) if you
   want to move to a newer version.

2. Copy `.env.example` to `.env` and set your OpenAI key:

   ```
   cp .env.example .env
   ```

   `OPENAI_API_KEY` can instead be set as a machine/profile environment
   variable — `python-dotenv` (used by `worker.py`/`starter.py`) never
   overrides a variable that's already set, so `.env` can leave it commented
   out in that case.

   To route through a LiteLLM gateway/proxy instead of OpenAI directly, set
   `OPENAI_API_BASE` to the gateway's URL (it must expose an
   OpenAI-compatible `/v1` API, which is what LiteLLM proxies expose).

   `.env` is gitignored, so your key never gets committed — only
   `.env.example` (which has no real credentials) is tracked.

   **Other providers / a custom org gateway**: `runner/agent_runner.py`
   always builds `LiteLlm(model=ADK_MODEL, ...)` directly, so switching
   provider is just changing `ADK_MODEL`'s prefix (`azure/...`,
   `anthropic/...`, `gemini/...`, `vertex_ai/...`, ...) — litellm reads that
   provider's own API-key/base-URL env vars the same way it reads
   `OPENAI_API_BASE` (see `.env.example` for concrete Azure/Gemini gateway
   examples). If your gateway needs a header beyond the provider's own API
   key (a tenant header, a second auth token, ...), set
   `AI_GATEWAY_HEADERS` — it's passed straight through as
   `LiteLlm(..., extra_headers=...)`, so it applies no matter which provider
   you're using, with no extra code per provider.

## Run

The pipeline needs three long-lived processes running at once: the Temporal
server, the worker, and (briefly) the starter. Use three separate terminals.

1. **Temporal dev server** — requires the [Temporal
   CLI](https://docs.temporal.io/cli). This also serves the Web UI at
   http://localhost:8233, which is the fastest way to watch a run and see
   exactly where it's stuck if something goes wrong.

   ```
   temporal server start-dev
   ```

2. **Worker** — polls the task queue and executes activities
   (`intake_activity`, `risk_scoring_activity`,
   `complexity_assessment_activity`, `triage_classification_activity`,
   `architecture_evaluator_activity`). Leave this running; it serves every
   workflow you start until you stop it.

   ```
   python worker.py
   ```

3. **Starter** — prompts you for each intake form field (press Enter to
   accept the shown default), starts one workflow run, and polls it until
   it completes, printing each stage's output. If an agent pauses to ask a
   clarifying question, `starter.py` prompts you for an answer right in the
   terminal and sends it back before continuing to poll. Use this, or the
   Streamlit UI below — both drive the same workflow the same way.

   ```
   python clients/starter.py
   ```

   Re-run step 3 for a new request any time; you don't need to restart the
   server or worker between runs.

### Optional: Streamlit UI

Instead of `starter.py`, run the small web UI (`app.py`) in that third
terminal — it starts the same workflow and polls it the same way, so the
Temporal server and worker still need to be running:

```
streamlit run clients/app.py
```

This opens a browser page with an intake form (API name, description,
requesting team, expected consumers, data sensitivity, optional
architecture notes) and a "Submit intake request" button. As the pipeline
runs, each agent's output (and any clarifying question) appears as a chat
message; if an agent is waiting on you, a chat input box appears inline for
your answer. Once the architecture evaluator finishes, all five result
sections render below the chat.

The default form values (`expected_consumers="TBD"`, a deliberately vague
`architecture_notes`) are chosen so a first-time run reliably shows all
three interruptible agents (intake, triage classification, architecture
evaluator) each asking a question — fill in real details yourself to see
an agent skip straight to its answer instead.

At the top of the page, a **"Recent runs"** panel lists every open or
recently-completed `IntakeWorkflow` run straight from Temporal (not from
browser session state) — paused runs waiting on your answer, in-progress
runs, and completed runs whose result you haven't seen yet. This means a
run is never lost even if you close the tab mid-conversation or reload
right after it finishes: reopen the page and pick it back up from any
browser session.

### Troubleshooting: workflow stuck at "Running"

If `temporal workflow list` shows a workflow stuck in `Running` with no
progress, check `temporal task-queue describe --task-queue
adk-agents-task-queue` for a poller. If a poller is listed but the backlog
count and dispatch rate aren't moving, the worker process is most likely
wedged (this can happen after a code change, a crashed `run_agent` call
mid-request, or a leftover process from an earlier run). Kill that worker
process and start a fresh `python worker.py` — the pending workflow task
will be picked up by the new poller and the run will resume without
restarting the workflow.

### Streamlit internals: running async Temporal calls from a sync script

`app.py`'s Temporal calls (`start_workflow`, `get_status`, `submit_answer`,
`list_open_workflows`) are all `async def`, but Streamlit script/fragment
code is plain synchronous Python. Bridging the two isn't just "call
`asyncio.run()`" — this Streamlit version executes script/fragment reruns
*inside its own already-running asyncio event loop*, so `asyncio.run()`
fails immediately with "cannot be called from a running event loop" every
time. `app.py` instead keeps one persistent background event loop alive in
a dedicated thread for the whole server process (`_get_background_loop()`,
memoized with `st.cache_resource` so it's a true process-wide singleton,
not recreated on every rerun) and dispatches every async call onto it via
`asyncio.run_coroutine_threadsafe(...).result(timeout=...)` (`run_async()`
in `app.py`). The explicit timeout matters: an early version that spun up
a fresh thread+loop per call instead reused the cached Temporal client
across a different loop each time, which could deadlock silently — a
fragment poll would just hang forever with nothing rendered, which looks
identical to "the UI does nothing." The timeout turns that failure mode
into a visible error instead.

One more rule this depends on: `get_temporal_client()` (a
`@st.cache_resource` function) and any `st.session_state` reads must only
ever be called from Streamlit's own thread, never from inside a coroutine
running on the background loop — Streamlit's caching/session APIs aren't
safe to touch from a thread without its own script context, and doing so
was a second real deadlock found during development. That's why every
async function in `app.py` takes the already-resolved `client` (and any
session values like `workflow_id`) as plain parameters instead of calling
`get_temporal_client()` or reading `st.session_state` itself.

## How It Works

1. **`starter.py`**/**`app.py`** connect to the Temporal server and call
   `client.start_workflow(...)`, which asks Temporal to schedule a new
   `IntakeWorkflow` run and immediately returns a handle — it does *not*
   block, since the run may need to pause for user input along the way.
   Temporal durably records the start as `WorkflowExecutionStarted` in the
   workflow's event history — that history, not any process's memory, is
   the source of truth for the run's state from here on.

2. **`worker.py`** is a long-lived process that opened a connection to
   Temporal and is continuously long-polling the `adk-agents-task-queue`
   task queue for work. It never talks to `starter.py`/`app.py` directly —
   both only ever talk to the Temporal server.

3. When Temporal has a task for the worker, the worker executes the
   matching code:
   - **Workflow tasks** run `IntakeWorkflow.run` (`workflow/intake_workflow.py`). This
     method is plain `async` Python but must stay *deterministic* — no
     network calls, no randomness, no real timers — because Temporal may
     replay it from history to rebuild state (e.g. after a worker
     restart). That's why it only ever calls
     `workflow.execute_activity(...)` (or `asyncio.gather` over several of
     them — still deterministic, since the actual I/O happens inside the
     activities, not the gather itself) rather than doing any real work
     itself.
   - **Activity tasks** run the actual side-effecting code — the five
     `*_activity` functions, one per `agents/<name>/activity.py`. Activities
     are where non-determinism is allowed, so this is where the real OpenAI
     calls happen.

4. Each activity calls `run_agent(...)` (`runner/agent_runner.py`), which builds a
   fresh Google ADK `LlmAgent` wired to `LiteLlm(model=ADK_MODEL)` and runs
   one turn through an ADK `Runner`. LiteLLM translates that into an
   OpenAI-compatible chat completion request sent to `OPENAI_API_BASE`
   (OpenAI directly, or your gateway if set) using `OPENAI_API_KEY`. Unless
   called with `allow_clarification=False`, the agent's instruction has a
   fixed clarification convention appended: if it needs more information
   before it can do a good job, it must reply with exactly
   `CLARIFY_NEEDED: <question>`; `run_agent` parses that prefix and returns
   `needs_clarification`/`question` alongside the normal output.
   `risk_scoring_activity` and `complexity_assessment_activity` pass
   `allow_clarification=False` and are told to state assumptions instead of
   asking — see the parallel-execution note below for why.

5. The workflow runs four stages (`IntakeWorkflow.run`):
   - `intake_activity` structures the raw form into a canonical summary,
     via the reusable `_run_stage` helper (can pause for clarification).
   - `risk_scoring_activity` and `complexity_assessment_activity` are
     dispatched **together and awaited together** via `asyncio.gather`,
     not through `_run_stage`:
     ```python
     risk_response, complexity_response = await asyncio.gather(
         workflow.execute_activity(risk_scoring_activity, ...),
         workflow.execute_activity(complexity_assessment_activity, ...),
     )
     ```
     `workflow.execute_activity(...)` returns immediately with an awaitable
     handle; wrapping two of them in `asyncio.gather` schedules both
     activity tasks on the task queue right away and lets the worker pick
     both up concurrently rather than one after another — confirmed during
     development by checking the worker log: both activities' `Running ...`
     log lines land in the same second, and the second one starts before
     the first one's LLM call has returned.
   - `triage_classification_activity` synthesizes both parallel outputs
     into a routing decision, via `_run_stage` (can pause for
     clarification — its instruction points out that business
     urgency/priority is never captured anywhere in the intake form, so a
     well-justified routing decision should always ask about it).
   - `architecture_evaluator_activity` compares the form's optional
     `architecture_notes` against a hardcoded standards doc, via
     `_run_stage` (can pause for clarification, but is instructed to skip
     cleanly with no question when no notes were provided).

   Only three of the four stages can pause (intake, triage classification,
   architecture evaluator) — the two parallel activities can't. This is a
   deliberate POC simplification: the workflow's clarification state
   (`_pending_question` / `_latest_answer`) only tracks one pending
   question at a time, which is enough for stages that run sequentially,
   but two activities that can pause *simultaneously* would need two
   independent pending-question slots. Rather than build that, the two
   parallel activities are simply told never to ask.

   Each activity call has its own `start_to_close_timeout` and
   `RetryPolicy`, so if one OpenAI call times out or errors, Temporal
   retries *just that activity* — it doesn't repeat earlier stages' work or
   restart the workflow.

   A pausable stage can also loop more than once — if the user's answer
   doesn't actually resolve what the agent needed, it can legitimately ask
   again. There is no cap on this: a stage will keep looping through
   clarification rounds for as long as the agent keeps setting
   `needs_clarification=True`, with no forced-final-answer fallback.

   For a pausable stage, when an activity's response has
   `needs_clarification=True`, `_run_stage` doesn't move on — it records
   the question, sets workflow state (`_pending_question`), and calls
   `await workflow.wait_condition(...)` to suspend the stage until an
   answer arrives. This is where Temporal's **Update** and **Query** come
   in:
   - `get_status` is a `@workflow.query` — a read-only, synchronous
     snapshot of `{summary, stage, waiting_for_input, pending_question,
     transcript, is_complete, result}`. Both `starter.py` and `app.py` poll
     this in a loop to know what to show the user.
   - `submit_answer` is a `@workflow.update` — the user's answer comes in
     through it. Its `@submit_answer.validator` rejects the update (before
     it's even admitted to history) if there's no question currently
     pending or the answer is blank, so `starter.py`/`app.py` get a
     synchronous accept/reject rather than finding out on the next poll.
     On acceptance, the handler sets `_latest_answer`, which satisfies the
     `wait_condition` and lets `_run_stage` fold the Q&A into `context` and
     re-run the same activity so the agent sees the answer.
   - Update/Query were chosen over Signals here specifically because the
     validator gives synchronous, pre-history rejection of bad input —
     Signals are fire-and-forget with no equivalent response.

6. Once all four stages finish, `IntakeWorkflow.run` builds and stores an
   `IntakeResult`, marks `is_complete=True`, and returns. Temporal records
   `WorkflowExecutionCompleted`; `starter.py`/`app.py` see `is_complete` on
   their next poll and fetch the final result via `handle.result()`
   (`starter.py`) or the query's embedded `result` field (`app.py`).

The key idea `worker.py`/`starter.py`/`app.py` demonstrate: Temporal is the
orchestrator and durable state store, not just a task queue. If the worker
process dies mid-pipeline — including while a workflow is paused waiting on
a user's answer — the event history already has every completed activity
result and any state set by accepted updates recorded, so a new worker
process can resume exactly where the old one left off instead of restarting
the whole pipeline (and therefore re-paying for OpenAI calls that already
succeeded).

### Supporting attachments

The intake form accepts one optional supporting file (PDF, image, or
text/markdown). The client (`starter.py`/`app.py`) reads it once and calls
`get_attachment_store().put(bytes, filename)`, getting back an opaque `ref`
string that goes into `IntakeForm.attachment_ref` — never a filesystem path
(the workflow may run on a different host than the client) and never raw
bytes directly on the form (keeps workflow-history payloads small).
`agents/intake/activity.py` resolves the ref back to bytes and
`agents/intake/attachment.py` dispatches by file extension: PDFs are
text-extracted with `pypdf`, text-like files are decoded as UTF-8, and
images are kept as raw bytes and handed to the model as a real image part
(`agents/intake/activity.py` → `run_agent(..., image_bytes=...)`) so a
vision-capable model actually sees it. Storage backend is chosen via the
`ATTACHMENT_STORE` env var — `inline` (default, no infra, bounded by
Temporal's own payload size limits) or `azure_blob` (persists externally,
no size ceiling; requires `AZURE_STORAGE_CONNECTION_STRING`).

### Amending earlier-stage info mid-run

`submit_amendment(field, value)` (a `@workflow.update`) lets you correct a
field you already submitted even after later stages have run, or while one
is paused asking its own question. Each field maps to the earliest stage
that depends on it (`AMENDABLE_FIELDS` in `workflow/intake_workflow.py`) —
correcting an intake-form field re-runs everything from intake onward;
correcting `architecture_notes` only re-runs the architecture evaluator,
since nothing else consumes it. If an amendment arrives while a stage is
paused on its own clarification question, that question is abandoned (not
answered) and the pipeline restarts from the amended stage immediately;
otherwise it's picked up at the next stage boundary. `starter.py` exposes
this as an `amend <field>=<value>` command typeable at any time; `app.py`
exposes it as a "Correct earlier info" form.

## Notes

- `runner/agent_runner.py` builds a fresh ADK `LlmAgent` + `Runner` per
  activity call, keeping each activity stateless and safe to retry.
- `LiteLlm(model="openai/gpt-4o-mini")` is ADK's documented way to route a
  model through LiteLLM; change `ADK_MODEL` in `.env` to use a different
  OpenAI model (or any other LiteLLM-supported provider/gateway).
- Activities (not the workflow) do all ADK/LiteLLM/network work, since
  Temporal workflow code must be deterministic and the ADK SDK is not
  sandbox-safe.
- `ARCHITECTURE_STANDARDS` in `agents/architecture_evaluator/prompt.py` is a
  short invented-but-plausible baseline (versioned endpoints, OAuth2/mTLS,
  encryption + audit logging for sensitive data, statelessness, shared
  gateway reuse) for the architecture evaluator to compare against — swap
  it for your organization's real standard whenever this stops being a POC.
- Whether an interruptible agent asks a clarifying question is a genuine
  LLM decision guided by its instruction, not a hardcoded trigger — it's
  told what a complete answer requires and to ask rather than guess when
  the input doesn't meet that bar, not told a specific question to ask. It
  is therefore not mathematically 100% deterministic, but the default form
  values (`expected_consumers="TBD"`, a deliberately vague
  `architecture_notes`, and the triage agent being told business
  urgency/priority is *always* absent from the intake) are tuned so all
  three interruptible agents ask reliably in practice — fill in complete,
  unambiguous details yourself to see an agent skip straight to its answer
  instead.
- The worker's very first LiteLLM/ADK call does some heavy synchronous
  imports that can briefly stall its event loop, which can make an early
  `get_status` query time out even though the workflow is healthy.
  `starter.py` retries transient query timeouts automatically; `app.py`'s
  polling fragment distinguishes transient RPC errors (retries silently)
  from permanent ones like a stale/nonexistent `workflow_id` (surfaces a
  clear error with a way to reset). This only affects the first call after
  a fresh worker start.
- `app.py` logs to `app_debug.log` (gitignored, INFO level) — useful for
  seeing exactly what a live server did (e.g. which stage a run is stuck
  on, or an unexpected exception) without needing browser DevTools access.
