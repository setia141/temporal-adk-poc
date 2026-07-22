import asyncio
import logging
import os

import litellm
from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.contrib.google_adk_agents import GoogleAdkPlugin
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from agents.intake import load_attachment_activity
from runner.gateway_gemini import register_gateway_gemini_if_configured
from workflow import IntakeWorkflow

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Custom gateway headers (e.g. an API key header your gateway wants beyond
# OPENAI_API_KEY, a tenant/routing header, etc). LLMRegistry.new_llm(...)
# rebuilds a fresh LiteLlm from just the model name inside invoke_model, so
# there's no per-call way to inject extra_headers through that path — this
# process-global fallback is what litellm's own completion() calls use
# whenever no explicit `headers` kwarg is passed. Setting it here (worker.py
# runs unsandboxed, unlike workflow code) applies it to every model call.
# Covers every litellm-routed ADK_MODEL (openai/..., azure/..., anthropic/...).
if gateway_headers := os.environ.get("AI_GATEWAY_HEADERS"):
    litellm.headers = dict(
        pair.split("=", 1) for pair in gateway_headers.split(",") if pair
    )

# Bare model names (gemini-...) skip litellm entirely and call google.genai
# directly, so the litellm.headers line above doesn't cover them — this reuses
# the same AI_GATEWAY_HEADERS env var for that path. See gateway_gemini.py.
register_gateway_gemini_if_configured()


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
        # ADK's content flow imports openai/litellm inside workflow code (not the
        # activity) to resolve the model backend. The plugin only passes through
        # google.adk/google.genai/mcp, so the first sandboxed import of these two
        # heavy SDKs blows Temporal's 2s deadlock-detector budget. Passing them
        # through here means the plugin's own passthrough additions layer on top
        # of this instead of replacing it.
        workflow_runner=SandboxedWorkflowRunner(
            restrictions=SandboxRestrictions.default.with_passthrough_modules(
                "openai", "litellm"
            )
        ),
    )

    logging.info("Starting worker on task queue '%s' (%s)", task_queue, address)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
