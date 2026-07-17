import asyncio
import concurrent.futures
import logging
import os
import threading
import uuid

import streamlit as st
import temporalio.client
import temporalio.service
from dotenv import load_dotenv
from temporalio.client import Client

from shared import IntakeForm
from workflow import IntakeWorkflow

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(message)s",
    handlers=[logging.FileHandler("app_debug.log", mode="a")],
)
log = logging.getLogger("app")

@st.cache_resource
def _get_background_loop() -> asyncio.AbstractEventLoop:
    """A single persistent event loop, shared by every rerun of every
    session for the life of this server process.

    This Streamlit version executes script/fragment reruns inside its own
    already-running asyncio loop, so a plain `asyncio.run(coro)` fails with
    "asyncio.run() cannot be called from a running event loop". Running
    coroutines on a separate loop via `asyncio.run_coroutine_threadsafe`
    avoids that — but the loop must be a true process-wide singleton (via
    `st.cache_resource`, since Streamlit re-executes top-level script code
    on every full rerun and a bare module-level variable would not survive
    that) so that `get_temporal_client()` below is always used from the
    exact loop it was created in.
    """
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    return loop


def run_async(coro, timeout: float = 30.0):
    """Run a coroutine on the shared background loop, with a timeout so an
    unexpected hang surfaces as a visible error instead of silently
    blocking the UI forever."""
    loop = _get_background_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        name = getattr(coro, "__name__", repr(coro))
        log.warning("run_async TIMEOUT after %.0fs: %s", timeout, name)
        future.cancel()
        raise TimeoutError(
            f"Temporal call did not respond within {timeout:.0f}s — the "
            "worker or Temporal server may be unreachable."
        )

st.set_page_config(page_title="API Intake Triage", page_icon=":material/inbox:")
st.title("API Intake Triage")
st.caption(
    "Intake Preparation → Triage (parallel risk + complexity scoring) → "
    "Architecture Evaluator, orchestrated as Temporal activities. Any "
    "agent may pause to ask you a clarifying question."
)

st.session_state.setdefault("workflow_id", None)
st.session_state.setdefault("running", False)


def _temporal_config() -> dict:
    return {
        "address": os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        "namespace": os.environ.get("TEMPORAL_NAMESPACE", "default"),
        "task_queue": os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue"),
    }


@st.cache_resource
def get_temporal_client() -> Client:
    """One shared Temporal connection per Streamlit server process.

    Without this, every 2-second poll from every open browser tab opened a
    brand-new gRPC connection (`Client.connect(...)`) and never closed it.
    Caching it is also what makes the persistent-loop approach in
    `run_async` correct: the client is created here via `run_async` (so on
    `_loop`), and every later call also runs on `_loop`, so it's always
    used from the same event loop it was created in. See Streamlit's
    caching guidance: connections/API clients belong in
    `st.cache_resource`, not reopened per rerun.
    """
    config = _temporal_config()
    log.info("Connecting to Temporal at %s", config["address"])
    return run_async(Client.connect(config["address"], namespace=config["namespace"]))


async def start_workflow(client: Client, form: IntakeForm, task_queue: str) -> str:
    workflow_id = f"intake-{uuid.uuid4()}"
    await client.start_workflow(
        IntakeWorkflow.run,
        form,
        id=workflow_id,
        task_queue=task_queue,
    )
    return workflow_id


async def get_status(client: Client, workflow_id: str):
    handle = client.get_workflow_handle(workflow_id)
    return await handle.query(IntakeWorkflow.get_status)


async def list_open_workflows(client: Client, limit: int = 20):
    """Running, paused, or recently-completed IntakeWorkflow executions,
    newest first, each paired with its live get_status() so the picker can
    show the actual summary and pending question/result rather than just
    an opaque workflow ID.

    Covers two cases: reconnecting to a run that's mid-conversation (e.g.
    paused waiting on a clarifying answer) even if the browser session
    that started it was closed or reloaded, and re-viewing a run that
    completed after the browser session watching it was closed or
    reloaded before the result was seen — the workflow and its result stay
    queryable in Temporal regardless of whether any UI is currently
    watching it.
    """
    executions = []
    async for execution in client.list_workflows(
        "WorkflowType='IntakeWorkflow' AND "
        "(ExecutionStatus='Running' OR ExecutionStatus='Completed')",
        limit=limit,
    ):
        executions.append(execution)
    executions.sort(key=lambda e: e.start_time, reverse=True)

    runs = []
    for execution in executions:
        try:
            status = await client.get_workflow_handle(execution.id).query(
                IntakeWorkflow.get_status
            )
        except Exception:
            continue
        runs.append((execution, status))
    return runs


async def submit_answer(client: Client, workflow_id: str, answer: str) -> None:
    handle = client.get_workflow_handle(workflow_id)
    await handle.execute_update(IntakeWorkflow.submit_answer, answer)


if not st.session_state.running:
    try:
        open_runs = run_async(list_open_workflows(get_temporal_client()))
    except Exception:
        open_runs = []

    if open_runs:
        with st.container(border=True):
            st.markdown("**Recent runs**")
            st.caption(
                "Pulled live from Temporal, not from this browser session — "
                "paused runs waiting on a clarifying answer, in-progress "
                "runs, and completed runs whose results you haven't seen "
                "yet all show up here, even after a reload."
            )
            labels = {}
            for execution, status in open_runs:
                if status.is_complete:
                    where = "completed — view result"
                elif status.waiting_for_input:
                    where = f"paused — {status.stage} asked: {status.pending_question}"
                else:
                    where = f"{status.stage} in progress"
                labels[f"\"{status.summary}\"  —  {where}"] = execution.id
            picked_label = st.selectbox("Recent runs", list(labels.keys()), label_visibility="collapsed")
            if st.button("Open selected run"):
                log.info("Opening run %s", labels[picked_label])
                st.session_state.workflow_id = labels[picked_label]
                st.session_state.running = True
                st.rerun()

if st.session_state.running:
    st.info("Viewing the run selected above.")
else:
    with st.form("intake_form", border=True):
        st.markdown("**New API intake request**")
        api_name = st.text_input("API name", value="Order Status API")
        description = st.text_area(
            "Description",
            value="Lets partner apps look up order status by order ID",
        )
        requesting_team = st.text_input("Requesting team", value="Fulfillment Platform")
        expected_consumers = st.text_input(
            "Expected consumers",
            value="TBD",
            help="Left as 'TBD' by default so you can see the intake agent ask a "
            "clarifying question — fill it in yourself to skip that.",
        )
        data_sensitivity = st.selectbox(
            "Data sensitivity",
            ["None", "Internal only", "PII", "Confidential/Regulated"],
            index=2,
        )
        architecture_notes = st.text_area(
            "Architecture notes (optional)",
            value="We plan to reuse some existing infrastructure but haven't finalized the design yet.",
            help="Left intentionally vague by default so you can see the architecture "
            "evaluator ask a clarifying question — clear this field entirely to see "
            "it skip the review instead, or fill in real details to see a real review.",
        )
        submitted = st.form_submit_button("Submit intake request", type="primary")

    if submitted:
        form = IntakeForm(
            api_name=api_name,
            description=description,
            requesting_team=requesting_team,
            expected_consumers=expected_consumers,
            data_sensitivity=data_sensitivity,
            architecture_notes=architecture_notes,
        )
        try:
            workflow_id = run_async(
                start_workflow(get_temporal_client(), form, _temporal_config()["task_queue"])
            )
        except Exception as exc:
            st.error(
                "Couldn't start the workflow. Is the Temporal server running "
                "(`temporal server start-dev`) and is `worker.py` running "
                f"and polling the task queue?\n\n{exc}"
            )
        else:
            log.info("Started workflow %s", workflow_id)
            st.session_state.workflow_id = workflow_id
            st.session_state.running = True
            st.rerun()


@st.fragment(run_every="2s")
def render_pipeline_status():
    if not st.session_state.workflow_id:
        # Deliberately independent of `running`: that flag only toggles the
        # form-vs-info-box in the outer script. `run_every="2s"` keeps
        # firing this fragment on its own timer regardless of `running`,
        # and a Streamlit fragment *replaces* its previously rendered
        # content every time it runs — so gating this on `running` too
        # meant that the instant a run completed and flipped `running` to
        # False, the very next 2s tick hit this guard, returned nothing,
        # and blanked out the results that had just been shown. Keying
        # only on workflow_id means a finished run's result stays
        # displayed on every subsequent tick instead of vanishing.
        return

    workflow_id = st.session_state.workflow_id
    try:
        status = run_async(get_status(get_temporal_client(), workflow_id))
    except temporalio.service.RPCError as exc:
        transient = {
            temporalio.service.RPCStatusCode.DEADLINE_EXCEEDED,
            temporalio.service.RPCStatusCode.UNAVAILABLE,
        }
        if exc.status in transient:
            # e.g. the worker briefly stalled on a cold-start import.
            # The fragment retries automatically on its next tick.
            st.caption(":material/progress_activity: Waiting on the worker...")
            return
        # A real, non-transient failure (e.g. NOT_FOUND — this workflow_id
        # doesn't exist, likely stale from a dev-server restart). Retrying
        # silently forever would just look like the UI is stuck doing
        # nothing, so surface it and let the user recover.
        st.error(f"Can't reach that run ({exc.status.name}): {exc.message}")
        if st.button("Forget this run and start over"):
            st.session_state.running = False
            st.session_state.workflow_id = None
            st.rerun()
        return
    except Exception as exc:
        log.exception("fragment: unexpected exception during get_status")
        st.error(f"Lost connection to the workflow: {exc}")
        return

    st.markdown(f"**Request:** {status.summary}")
    st.caption(f"Workflow ID: `{st.session_state.workflow_id}`")

    role_map = {"output": "assistant", "question": "assistant", "answer": "user"}
    for entry in status.transcript:
        label = "assistant" if entry.role == "answer" else f"**{entry.agent_name}**"
        with st.chat_message(role_map.get(entry.role, "assistant")):
            if entry.role != "answer":
                st.markdown(label)
            st.markdown(entry.text)

    if status.waiting_for_input:
        answer = st.chat_input(f"Answer {status.stage}'s question above...")
        if answer:
            try:
                run_async(submit_answer(get_temporal_client(), workflow_id, answer))
            except temporalio.client.WorkflowUpdateFailedError as exc:
                st.error(f"Answer rejected: {exc}")
            else:
                st.rerun()
    elif status.is_complete:
        st.session_state.running = False
        result = status.result
        st.subheader("Canonical Intake")
        st.markdown(result.canonical_intake)
        st.subheader("Risk Score")
        st.markdown(result.risk_score)
        st.subheader("Complexity Assessment")
        st.markdown(result.complexity_assessment)
        st.subheader("Triage Classification")
        st.markdown(result.classification)
        st.subheader("Architecture Review")
        st.markdown(result.architecture_review)
    else:
        st.caption(f":material/progress_activity: {status.stage} is working...")


render_pipeline_status()
