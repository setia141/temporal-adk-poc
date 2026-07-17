import logging

from temporalio import activity

from agent_runner import run_agent
from shared import AgentRequest, AgentResponse

logger = logging.getLogger(__name__)

ARCHITECTURE_STANDARDS = """
- APIs must be exposed as versioned REST endpoints (e.g. /v1/...) or gRPC
  services with proto-defined contracts. No unversioned or ad-hoc endpoints.
- All external-facing APIs must authenticate callers via OAuth2
  client-credentials or mTLS.
- Any API handling PII or confidential/regulated data must encrypt data at
  rest and in transit, and log access for audit purposes.
- Services must be stateless and horizontally scalable; any session state
  must live in an external store, not in-process memory.
- New APIs should reuse the shared API gateway and rate-limiting
  infrastructure rather than standing up bespoke gateways.
"""


@activity.defn
async def intake_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running intake preparation agent")
    result = await run_agent(
        name="intake_preparation",
        instruction=(
            "You are an Intake Preparation Agent for API onboarding "
            "requests. Given the raw form fields below, produce a concise "
            "canonical structured summary as a labeled list covering: "
            "purpose, requesting team, expected consumers, data "
            "sensitivity, and any technical requirements you can infer. "
            "This summary is the only input the downstream risk scoring, "
            "complexity assessment, and triage agents will see, so it must "
            "be accurate and complete enough for them to do their jobs."
        ),
        prompt=f"Raw intake form:\n{request.subject}\n\n{request.context}".strip(),
    )
    return AgentResponse(
        agent_name="intake_preparation",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )


@activity.defn
async def risk_scoring_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running risk scoring agent")
    result = await run_agent(
        name="risk_scoring",
        instruction=(
            "You are a risk scoring agent for API onboarding requests. "
            "Given the canonical intake summary, assign a risk level "
            "(Low, Medium, or High) based on data sensitivity and consumer "
            "exposure, with a one-paragraph rationale. Do not ask any "
            "questions — if information is incomplete, make a reasonable "
            "assumption and state it explicitly in your rationale."
        ),
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
    )
    return AgentResponse(agent_name="risk_scoring", output=result.output)


@activity.defn
async def complexity_assessment_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running complexity assessment agent")
    result = await run_agent(
        name="complexity_assessment",
        instruction=(
            "You are a complexity assessment agent for API onboarding "
            "requests. Given the canonical intake summary, assign a "
            "complexity rating (Low, Medium, or High) based on integration "
            "scope, number of consumers, and technical requirements, with "
            "a one-paragraph rationale. Do not ask any questions — if "
            "information is incomplete, make a reasonable assumption and "
            "state it explicitly in your rationale."
        ),
        prompt=f"Canonical intake:\n{request.subject}",
        allow_clarification=False,
    )
    return AgentResponse(agent_name="complexity_assessment", output=result.output)


@activity.defn
async def triage_classification_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running triage classification agent")
    result = await run_agent(
        name="triage_classification",
        instruction=(
            "You are a triage classification and routing agent for API "
            "onboarding requests. Given the canonical intake and its risk "
            "score and complexity assessment, decide a routing outcome "
            "(e.g. Fast-track, Standard Review, or Escalate to "
            "Architecture Board) with a one-line justification. This "
            "decision determines which team handles the request next, so "
            "get it right. A correct routing decision always depends on "
            "business urgency or priority — how soon the requesting team "
            "needs this live — and the canonical intake never captures "
            "this at all, so you never have it. Regardless of how clear "
            "the risk and complexity signals seem on their own, always "
            "ask about urgency/priority before finalizing your routing "
            "decision, since it is always missing information you need."
        ),
        prompt=f"Canonical intake:\n{request.subject}\n\n{request.context}",
    )
    return AgentResponse(
        agent_name="triage_classification",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )


@activity.defn
async def architecture_evaluator_activity(request: AgentRequest) -> AgentResponse:
    activity.logger.info("Running architecture evaluator agent")
    architecture_notes = request.context.strip() or "(none provided)"
    result = await run_agent(
        name="architecture_evaluator",
        instruction=(
            "You are an Architecture Evaluator agent for API onboarding "
            f"requests. Predefined architecture standards:\n{ARCHITECTURE_STANDARDS}\n"
            "Compare the user's architecture notes against these "
            "standards and list concrete alignment and misalignment "
            "points. If the notes are literally '(none provided)', state "
            "plainly that no architecture was submitted and no review was "
            "performed — never ask the user to supply one in that case."
        ),
        prompt=f"Canonical intake:\n{request.subject}\n\nUser-provided architecture notes:\n{architecture_notes}",
    )
    return AgentResponse(
        agent_name="architecture_evaluator",
        output=result.output,
        needs_clarification=result.needs_clarification,
        question=result.question,
    )
