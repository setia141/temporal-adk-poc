import asyncio
import logging
import os

from dotenv import find_dotenv, load_dotenv
from temporalio.client import Client
from temporalio.contrib.google_adk_agents import GoogleAdkPlugin
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

from agents.architecture_evaluator import fetch_architecture_standards_activity
from agents.complexity_assessment import lookup_downstream_dependencies_activity
from agents.intake import load_attachments_activity, lookup_requesting_team_activity
from agents.risk_scoring import lookup_prior_incidents_activity
from agents.triage_classification import lookup_team_review_capacity_activity
from runner.gateway_litellm import register_gateway_litellm_if_configured
from runner.llm_call_logger import register_llm_call_logger
from workflow import IntakeWorkflow

_dotenv_path = find_dotenv()
load_dotenv(_dotenv_path)
logging.basicConfig(level=logging.INFO)
logging.info(
    "Env loaded from: %s | ADK_MODEL=%s | AI_GATEWAY_BASE_URL=%s",
    _dotenv_path or "(no .env file found)",
    os.environ.get("ADK_MODEL", "(unset, default openai/gpt-4o-mini)"),
    os.environ.get("AI_GATEWAY_BASE_URL", "(unset)"),
)

# Custom gateway (base URL + extra headers) for every litellm-routed ADK_MODEL
# (openai/..., azure/..., anthropic/..., ...). invoke_model rebuilds the LLM
# from just the model-name string via LLMRegistry, so constructor kwargs can't
# be passed per-call — instead this re-registers a LiteLlm subclass with
# AI_GATEWAY_BASE_URL/AI_GATEWAY_HEADERS baked in. See gateway_litellm.py.
register_gateway_litellm_if_configured()

# Logs the exact outbound HTTP call (URL, headers with values masked) for
# every LLM invocation — see runner/llm_call_logger.py.
register_llm_call_logger()


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
        # activities (see _plugin.py). The hand-written activities are the
        # attachment loader plus one activity per agent tool (the real work
        # behind the workflow-side wrappers in agents/<name>/tools.py).
        activities=[
            load_attachments_activity,
            lookup_requesting_team_activity,
            lookup_prior_incidents_activity,
            lookup_downstream_dependencies_activity,
            lookup_team_review_capacity_activity,
            fetch_architecture_standards_activity,
        ],
        # ADK's content flow imports openai/litellm inside workflow code (not the
        # activity) to resolve the model backend, and the plugin only passes
        # google.adk/google.genai/mcp through the sandbox. On a cold machine that
        # first sandboxed import of two heavy SDKs can exceed Temporal's 2s
        # deadlock-detector budget ([TMPRL1101], observed on this machine —
        # wedges the worker, not just the task). Passing them through here is
        # cheap insurance; the plugin's own passthrough additions layer on top.
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
