import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv
from temporalio.client import Client

from workflow import ContentPipelineWorkflow

load_dotenv()


async def main() -> None:
    topic = " ".join(sys.argv[1:]) or "Why Temporal is a good fit for orchestrating LLM agent pipelines"

    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue")

    client = await Client.connect(address, namespace=namespace)

    workflow_id = f"content-pipeline-{uuid.uuid4()}"
    result = await client.execute_workflow(
        ContentPipelineWorkflow.run,
        topic,
        id=workflow_id,
        task_queue=task_queue,
    )

    print(f"\n=== Topic ===\n{result.topic}")
    print(f"\n=== Research (agent: researcher) ===\n{result.research}")
    print(f"\n=== Draft (agent: writer) ===\n{result.draft}")
    print(f"\n=== Review (agent: reviewer) ===\n{result.review}")


if __name__ == "__main__":
    asyncio.run(main())
