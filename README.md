# Temporal + Google ADK (LiteLLM -> OpenAI) POC

A minimal proof of concept showing Temporal orchestrating multiple Google ADK
agents, where each agent uses ADK's `LiteLlm` model wrapper to call OpenAI
models (optionally through a LiteLLM gateway/proxy).

## Architecture

```
starter.py --> Temporal Server --> worker.py
                                      |
                            ContentPipelineWorkflow (workflow.py)
                                      |
              +---------------+---------------+---------------+
              |                |               |
      research_activity   write_activity   review_activity      (activities.py)
              |                |               |
        ADK "researcher"  ADK "writer"    ADK "reviewer"        (agent_runner.py)
              |                |               |
              +----------- LiteLlm(model="openai/...") ---------+
                                      |
                              OpenAI API / gateway
```

Each agent runs as its own Temporal Activity, so it gets independent
retries, timeouts, and visibility in Temporal's UI/history, even though all
three agents are invoked sequentially by one workflow.

## Project Structure

```
shared.py       Dataclasses shared between workflow and activities
                 (AgentRequest, AgentResponse, PipelineResult)
activities.py    Temporal activities: research_activity, write_activity,
                 review_activity
agent_runner.py  Builds and runs a single ADK LlmAgent turn via LiteLlm
workflow.py      ContentPipelineWorkflow — chains the three activities
worker.py        Long-lived process that polls Temporal and executes
                 workflow/activity tasks
starter.py       CLI entry point: starts one workflow run and prints the
                 result
app.py           Streamlit UI: same starter.py logic behind a web page
requirements.txt Python dependencies
.env.example     Template for local environment/config
```

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
   (`research_activity`, `write_activity`, `review_activity`). Leave this
   running; it serves every workflow you start until you stop it.

   ```
   python worker.py
   ```

3. **Starter** — kicks off one workflow run and blocks until it completes,
   printing each agent's output. Use this, or the Streamlit UI below —
   both do the same thing.

   ```
   python starter.py "The benefits of durable execution for AI agents"
   ```

   Re-run step 3 with a different topic any time; you don't need to restart
   the server or worker between runs.

### Optional: Streamlit UI

Instead of `starter.py`, run the small web UI (`app.py`) in that third
terminal — it's a thin wrapper that calls the same
`client.execute_workflow(...)` starter.py uses, so the Temporal server and
worker still need to be running:

```
streamlit run app.py
```

This opens a browser page with a topic box and a "Run pipeline" button; the
research/draft/review output renders on the page once the workflow
completes.

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

## How It Works

1. **`starter.py`** connects to the Temporal server and calls
   `client.execute_workflow(...)`, which asks Temporal to schedule a new
   `ContentPipelineWorkflow` run and then blocks waiting for its result.
   Temporal durably records this as `WorkflowExecutionStarted` in the
   workflow's event history — that history, not any process's memory, is
   the source of truth for the run's state from here on.

2. **`worker.py`** is a long-lived process that opened a connection to
   Temporal and is continuously long-polling the `adk-agents-task-queue`
   task queue for work. It never talks to `starter.py` directly — both only
   ever talk to the Temporal server.

3. When Temporal has a task for the worker, the worker executes the
   matching code:
   - **Workflow tasks** run `ContentPipelineWorkflow.run` (`workflow.py`).
     This method is plain `async` Python but must stay *deterministic* — no
     network calls, no randomness, no real timers — because Temporal may
     replay it from history to rebuild state (e.g. after a worker restart).
     That's why it only ever calls `workflow.execute_activity(...)` rather
     than doing any real work itself.
   - **Activity tasks** run the actual side-effecting code:
     `research_activity`, `write_activity`, `review_activity`
     (`activities.py`). Activities are where non-determinism is allowed, so
     this is where the real OpenAI calls happen.

4. Each activity calls `run_agent(...)` (`agent_runner.py`), which builds a
   fresh Google ADK `LlmAgent` wired to `LiteLlm(model=ADK_MODEL)` and runs
   one turn through an ADK `Runner`. LiteLLM translates that into an
   OpenAI-compatible chat completion request sent to `OPENAI_API_BASE`
   (OpenAI directly, or your gateway if set) using `OPENAI_API_KEY`. The
   final response text is returned back up through the activity to the
   workflow.

5. The workflow chains the three activities sequentially, threading each
   agent's output into the next agent's input as `context`
   (`AgentRequest`/`AgentResponse`/`PipelineResult` in `shared.py`):
   `research_activity` → notes → `write_activity` → draft →
   `review_activity` → feedback. Each activity call has its own
   `start_to_close_timeout` and `RetryPolicy`, so if one OpenAI call times
   out or errors, Temporal retries *just that activity* (up to 3 attempts,
   exponential backoff) — it doesn't repeat the earlier agents' work or
   restart the workflow.

6. Once `review_activity` completes, `ContentPipelineWorkflow.run` returns a
   `PipelineResult`. Temporal records `WorkflowExecutionCompleted`, delivers
   the result back to the still-waiting `execute_workflow(...)` call in
   `starter.py`, and `starter.py` prints it.

The key idea `worker.py`/`starter.py` demonstrate: Temporal is the
orchestrator and durable state store, not just a task queue. If the worker
process dies mid-pipeline, the event history already has every completed
activity result recorded, so a new worker process can resume exactly where
the old one left off instead of restarting the whole pipeline (and
therefore re-paying for OpenAI calls that already succeeded) — which is
what let restarting the wedged worker earlier resume the stuck run instead
of losing it.

## Notes

- `agent_runner.py` builds a fresh ADK `LlmAgent` + `Runner` per activity
  call, keeping each activity stateless and safe to retry.
- `LiteLlm(model="openai/gpt-4o-mini")` is ADK's documented way to route a
  model through LiteLLM; change `ADK_MODEL` in `.env` to use a different
  OpenAI model (or any other LiteLLM-supported provider/gateway).
- Activities (not the workflow) do all ADK/LiteLLM/network work, since
  Temporal workflow code must be deterministic and the ADK SDK is not
  sandbox-safe.
