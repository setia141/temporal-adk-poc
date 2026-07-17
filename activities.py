import logging

from temporalio import activity

from agent_runner import run_agent
from shared import AgentRequest, AgentResponse

logger = logging.getLogger(__name__)


@activity.defn
async def research_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running researcher agent for topic: %s", request.topic)
    output = await run_agent(
        name="researcher",
        instruction=(
            "You are a research analyst. Given a topic, produce a concise, "
            "factual bullet-point summary of the key points a writer would "
            "need to write about it. Do not write prose, only bullet points."
        ),
        prompt=f"Topic: {request.topic}",
    )
    return AgentResponse(agent_name="researcher", output=output)


@activity.defn
async def write_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running writer agent for topic: %s", request.topic)
    output = await run_agent(
        name="writer",
        instruction=(
            "You are a technical writer. Using the research notes provided, "
            "write a short, clear 2-3 paragraph article on the topic."
        ),
        prompt=f"Topic: {request.topic}\n\nResearch notes:\n{request.context}",
    )
    return AgentResponse(agent_name="writer", output=output)


@activity.defn
async def review_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running reviewer agent for topic: %s", request.topic)
    output = await run_agent(
        name="reviewer",
        instruction=(
            "You are an editor. Review the draft article for clarity, "
            "accuracy, and tone. Return a short list of actionable "
            "feedback points, or 'Looks good' if no changes are needed."
        ),
        prompt=f"Topic: {request.topic}\n\nDraft:\n{request.context}",
    )
    return AgentResponse(agent_name="reviewer", output=output)
