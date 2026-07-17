import asyncio
import os
import uuid

import temporalio.client
import temporalio.service
from dotenv import load_dotenv
from temporalio.client import Client

from shared import IntakeForm
from workflow import IntakeWorkflow

load_dotenv()


def _prompt(label: str, default: str) -> str:
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def collect_form() -> IntakeForm:
    print("New API intake request (press Enter to accept the [default]):\n")
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
    )


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

    while True:
        status = await _query_status(handle)
        if status.is_complete:
            break
        if status.waiting_for_input:
            answer = input(f"\n[{status.stage} needs clarification] {status.pending_question}\n> ")
            try:
                await handle.execute_update(IntakeWorkflow.submit_answer, answer)
            except temporalio.client.WorkflowUpdateFailedError as exc:
                print(f"Answer rejected: {exc}")
        else:
            await asyncio.sleep(1)

    result = await handle.result()

    print(f"\n=== Canonical Intake ===\n{result.canonical_intake}")
    print(f"\n=== Risk Score ===\n{result.risk_score}")
    print(f"\n=== Complexity Assessment ===\n{result.complexity_assessment}")
    print(f"\n=== Triage Classification ===\n{result.classification}")
    print(f"\n=== Architecture Review ===\n{result.architecture_review}")


if __name__ == "__main__":
    asyncio.run(main())
