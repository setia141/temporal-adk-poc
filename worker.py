import asyncio
import logging
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from activities import research_activity, write_activity, review_activity
from workflow import ContentPipelineWorkflow

load_dotenv()
logging.basicConfig(level=logging.INFO)


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue")

    client = await Client.connect(address, namespace=namespace)

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[ContentPipelineWorkflow],
        activities=[research_activity, write_activity, review_activity],
    )

    logging.info("Starting worker on task queue '%s' (%s)", task_queue, address)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
