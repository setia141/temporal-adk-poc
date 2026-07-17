import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import (
        architecture_evaluator_activity,
        complexity_assessment_activity,
        intake_activity,
        risk_scoring_activity,
        triage_classification_activity,
    )
    from shared import AgentRequest, IntakeForm, IntakeResult, IntakeStatus, TranscriptEntry

DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

MAX_CLARIFICATION_ROUNDS = 2


@workflow.defn
class IntakeWorkflow:
    """Orchestrates an API intake request through three agents: intake
    preparation, triage (risk scoring + complexity assessment run in
    parallel, then a classification/routing decision), and architecture
    evaluation. Any interruptible stage can pause to ask the user a
    clarifying question via the submit_answer update, which the
    get_status query surfaces to callers."""

    def __init__(self) -> None:
        self._summary: str = ""
        self._pending_agent: str = ""
        self._pending_question: str = ""
        self._latest_answer: str | None = None
        self._transcript: list[TranscriptEntry] = []
        self._current_stage: str = "intake"
        self._done: bool = False
        self._result: IntakeResult | None = None

    async def _run_stage(self, agent_name: str, activity_fn, request: AgentRequest):
        self._current_stage = agent_name
        rounds = 0
        while True:
            response = await workflow.execute_activity(
                activity_fn,
                request,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY_POLICY,
            )
            if not response.needs_clarification:
                self._transcript.append(TranscriptEntry(agent_name, "output", response.output))
                return response

            rounds += 1
            self._transcript.append(TranscriptEntry(agent_name, "question", response.question))
            self._pending_agent = agent_name
            self._pending_question = response.question
            await workflow.wait_condition(lambda: self._latest_answer is not None)

            self._transcript.append(TranscriptEntry(agent_name, "answer", self._latest_answer))
            addendum = f"\n\nClarification Q: {response.question}\nA: {self._latest_answer}"
            if rounds >= MAX_CLARIFICATION_ROUNDS:
                # Guards against a stage looping forever if the model keeps
                # finding something to ask about (observed in testing: an
                # agent can re-ask a near-identical question indefinitely
                # despite receiving a relevant answer each time). After the
                # cap, force exactly one more call that must produce a real
                # final answer, then accept that call's output unconditionally
                # even if it still nominally claims to need clarification.
                addendum += (
                    "\n\n(This is your final chance to answer. You must "
                    "produce your complete, final answer now using the "
                    "information above — do not ask any further questions, "
                    "even if some detail still feels uncertain; make your "
                    "best judgment call.)"
                )
            request.context = (request.context + addendum).strip()
            self._latest_answer = None
            self._pending_question = ""
            self._pending_agent = ""

            if rounds >= MAX_CLARIFICATION_ROUNDS:
                final_response = await workflow.execute_activity(
                    activity_fn,
                    request,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DEFAULT_RETRY_POLICY,
                )
                self._transcript.append(
                    TranscriptEntry(agent_name, "output", final_response.output)
                )
                return final_response

    @workflow.run
    async def run(self, form: IntakeForm) -> IntakeResult:
        self._summary = f"{form.api_name} — {form.requesting_team}"
        form_text = (
            f"API name: {form.api_name}\n"
            f"Description: {form.description}\n"
            f"Requesting team: {form.requesting_team}\n"
            f"Expected consumers: {form.expected_consumers}\n"
            f"Data sensitivity: {form.data_sensitivity}"
        )

        intake = await self._run_stage("intake", intake_activity, AgentRequest(subject=form_text))

        self._current_stage = "triage"
        risk_response, complexity_response = await asyncio.gather(
            workflow.execute_activity(
                risk_scoring_activity,
                AgentRequest(subject=intake.output),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY_POLICY,
            ),
            workflow.execute_activity(
                complexity_assessment_activity,
                AgentRequest(subject=intake.output),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY_POLICY,
            ),
        )
        self._transcript.append(TranscriptEntry("risk_scoring", "output", risk_response.output))
        self._transcript.append(
            TranscriptEntry("complexity_assessment", "output", complexity_response.output)
        )

        triage = await self._run_stage(
            "triage_classification",
            triage_classification_activity,
            AgentRequest(
                subject=intake.output,
                context=(
                    f"Risk score:\n{risk_response.output}\n\n"
                    f"Complexity assessment:\n{complexity_response.output}"
                ),
            ),
        )

        architecture = await self._run_stage(
            "architecture_evaluator",
            architecture_evaluator_activity,
            AgentRequest(subject=intake.output, context=form.architecture_notes),
        )

        self._current_stage = "done"
        self._done = True
        self._result = IntakeResult(
            canonical_intake=intake.output,
            risk_score=risk_response.output,
            complexity_assessment=complexity_response.output,
            classification=triage.output,
            architecture_review=architecture.output,
        )
        return self._result

    @workflow.update
    async def submit_answer(self, answer: str) -> str:
        self._latest_answer = answer
        return "accepted"

    @submit_answer.validator
    def _validate_submit_answer(self, answer: str) -> None:
        if not self._pending_question:
            raise ValueError("No clarification question is currently pending.")
        if not answer or not answer.strip():
            raise ValueError("Answer cannot be blank.")

    @workflow.query
    def get_status(self) -> IntakeStatus:
        return IntakeStatus(
            summary=self._summary,
            stage=self._current_stage,
            waiting_for_input=bool(self._pending_question),
            pending_question=self._pending_question,
            transcript=list(self._transcript),
            is_complete=self._done,
            result=self._result,
        )
