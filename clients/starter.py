import asyncio
import os
import sys
import uuid
from pathlib import Path

# clients/ is a subfolder, but shared/storage/workflow/agents/runner are
# top-level packages at the repo root — make sure that root is importable
# regardless of the cwd this script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import temporalio.client
import temporalio.service
from dotenv import load_dotenv
from temporalio.client import Client

from shared import IntakeForm
from storage import get_attachment_store
from workflow import AMENDABLE_FIELDS, IntakeWorkflow

load_dotenv()


def _prompt(label: str, default: str) -> str:
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def collect_form() -> IntakeForm:
    print("New API intake request (press Enter to accept the [default]):\n")
    attachment_path = _prompt(
        "Supporting attachment path (PDF/image/text, optional, blank to skip)", ""
    )
    attachment_ref, attachment_filename = "", ""
    if attachment_path:
        with open(attachment_path, "rb") as f:
            attachment_ref = get_attachment_store().put(f.read(), os.path.basename(attachment_path))
        attachment_filename = os.path.basename(attachment_path)

    return IntakeForm(
        api_name=_prompt("API name", "Order Status API"),
        description=_prompt("Description", "Lets partner apps look up order status by order ID"),
        requesting_team=_prompt("Requesting team", "Fulfillment Platform"),
        expected_consumers=_prompt("Expected consumers", "TBD"),
        data_sensitivity=_prompt("Data sensitivity (None/Internal only/PII/Confidential)", "PII"),
        architecture_notes=_prompt(
            "Architecture notes (optional)",
            "We plan to reuse some existing infrastructure but haven't finalized the design yet.",
        ),
        attachment_ref=attachment_ref,
        attachment_filename=attachment_filename,
    )


async def _read_line(prompt: str) -> str:
    """Reads one line of stdin without blocking the event loop, so the
    status-polling loop can keep running while waiting for input.

    # ponytail: cancelling the returned future (done when a clarification
    # prompt takes over) does not stop the underlying input() call in its
    # thread — it keeps waiting on stdin and will silently swallow the next
    # line typed after cancellation. Harmless in practice (there's always a
    # fresh prompt asking for exactly one line right after), but if this
    # starts causing stray-input confusion, switch to a real cross-platform
    # cancellable stdin reader (e.g. a small prompt_toolkit-based one).
    """
    return await asyncio.get_event_loop().run_in_executor(None, input, prompt)


async def _handle_amend_command(handle, command: str) -> None:
    field, _, value = command[len("amend "):].partition("=")
    field, value = field.strip(), value.strip()
    if field not in AMENDABLE_FIELDS:
        print(f"Unknown field '{field}'. Valid fields: {sorted(AMENDABLE_FIELDS)}")
        return
    try:
        await handle.execute_update(IntakeWorkflow.submit_amendment, args=[field, value])
        print(f"Queued correction: {field} = {value}")
    except temporalio.client.WorkflowUpdateFailedError as exc:
        print(f"Correction rejected: {exc}")


async def _query_status(handle):
    """Query get_status, retrying transient RPC timeouts.

    The worker's first LiteLLM/ADK call does heavy synchronous imports that
    can briefly stall its event loop, which can make an early query time out
    even though the workflow is healthy and progressing.
    """
    for attempt in range(5):
        try:
            return await handle.query(IntakeWorkflow.get_status)
        except temporalio.service.RPCError:
            if attempt == 4:
                raise
            await asyncio.sleep(2)


async def main() -> None:
    form = collect_form()

    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue")

    client = await Client.connect(address, namespace=namespace)

    workflow_id = f"intake-{uuid.uuid4()}"
    handle = await client.start_workflow(
        IntakeWorkflow.run,
        form,
        id=workflow_id,
        task_queue=task_queue,
    )

    print(
        f"\nTip: at any time, type 'amend <field>=<value>' to correct info "
        f"already submitted (fields: {', '.join(sorted(AMENDABLE_FIELDS))}). "
        "It takes effect at the next stage checkpoint, re-running that "
        "stage and everything after it.\n"
    )

    command_task = asyncio.ensure_future(_read_line("> "))
    while True:
        status = await _query_status(handle)
        if status.is_complete:
            command_task.cancel()
            break

        if status.waiting_for_input:
            if not command_task.done():
                command_task.cancel()
            answer = input(f"\n[{status.stage} needs clarification] {status.pending_question}\n> ")
            if answer.lower().startswith("amend "):
                await _handle_amend_command(handle, answer)
            else:
                try:
                    await handle.execute_update(IntakeWorkflow.submit_answer, answer)
                except temporalio.client.WorkflowUpdateFailedError as exc:
                    print(f"Answer rejected: {exc}")
            command_task = asyncio.ensure_future(_read_line("> "))
        else:
            done, _ = await asyncio.wait({command_task}, timeout=1)
            if command_task in done:
                command = command_task.result()
                if command.lower().startswith("amend "):
                    await _handle_amend_command(handle, command)
                elif command.strip():
                    print("Unrecognized input (try: amend <field>=<value>)")
                command_task = asyncio.ensure_future(_read_line("> "))

    result = await handle.result()

    print(f"\n=== Canonical Intake ===\n{result.canonical_intake}")
    print(f"\n=== Risk Score ===\n{result.risk_score}")
    print(f"\n=== Complexity Assessment ===\n{result.complexity_assessment}")
    print(f"\n=== Triage Classification ===\n{result.classification}")
    print(f"\n=== Architecture Review ===\n{result.architecture_review}")


if __name__ == "__main__":
    asyncio.run(main())
