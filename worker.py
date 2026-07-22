import asyncio
import logging
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.contrib.google_adk_agents import GoogleAdkPlugin
from temporalio.worker import Worker

from agents.intake import load_attachment_activity
from workflow import IntakeWorkflow

load_dotenv()
logging.basicConfig(level=logging.INFO)


async def main() -> None:
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue")

    client = await Client.connect(address, namespace=namespace, plugins=[GoogleAdkPlugin()])

    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[IntakeWorkflow],
        # GoogleAdkPlugin auto-registers its own invoke_model/invoke_model_streaming
        # activities (see _plugin.py) — load_attachment_activity is the only
        # activity this project still defines by hand.
        activities=[load_attachment_activity],
    )

    logging.info("Starting worker on task queue '%s' (%s)", task_queue, address)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
