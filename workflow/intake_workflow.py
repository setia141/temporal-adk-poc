import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from agents.architecture_evaluator import architecture_evaluator_activity
    from agents.complexity_assessment import complexity_assessment_activity
    from agents.intake import intake_activity
    from agents.risk_scoring import risk_scoring_activity
    from agents.triage_classification import triage_classification_activity
    from shared import AgentRequest, IntakeForm, IntakeResult, IntakeStatus, TranscriptEntry

DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)

STAGE_ORDER = ["intake", "triage", "architecture_evaluator"]

# Which stage must re-run when a field is amended after the fact — a field
# feeds forward into every later stage, so amending it re-runs everything
# from that point on, discarding whatever those stages had already produced.
AMENDABLE_FIELDS = {
    "api_name": "intake",
    "description": "intake",
    "requesting_team": "intake",
    "expected_consumers": "intake",
    "data_sensitivity": "intake",
    "architecture_notes": "architecture_evaluator",
}


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
        self._form: IntakeForm | None = None
        self._pending_amendment: str | None = None  # a STAGE_ORDER name, or None
        self._awaiting_confirmation: bool = False

    async def _run_stage(self, agent_name: str, activity_fn, request: AgentRequest):
        """Returns an AgentResponse, or None if an amendment arrived while
        this stage was paused on its own clarification question — in that
        case the current question is abandoned rather than answered, since
        an earlier-stage amendment is about to make it moot."""
        self._current_stage = agent_name
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

            # response.output (not response.question) is what's shown to the
            # user: when the agent is answering a follow-up question before
            # re-asking (see CLARIFY_CONVENTION), response.output is that
            # full conversational reply — response.question alone would be
            # just the bare re-asked question, silently dropping the answer.
            self._transcript.append(TranscriptEntry(agent_name, "question", response.output))
            self._pending_agent = agent_name
            self._pending_question = response.output
            await workflow.wait_condition(
                lambda: self._latest_answer is not None or self._pending_amendment is not None
            )

            if self._pending_amendment is not None:
                self._pending_question = ""
                self._pending_agent = ""
                return None

            self._transcript.append(TranscriptEntry(agent_name, "answer", self._latest_answer))
            addendum = f"\n\nClarification Q: {response.question}\nA: {self._latest_answer}"
            request.context = (request.context + addendum).strip()
            self._latest_answer = None
            self._pending_question = ""
            self._pending_agent = ""

    @workflow.run
    async def run(self, form: IntakeForm) -> IntakeResult:
        self._form = form  # shared reference: submit_amendment mutates this in place
        self._summary = f"{form.api_name} — {form.requesting_team}"

        intake = risk_response = complexity_response = triage = architecture = None
        restart_from = "intake"

        while True:
            self._pending_amendment = None

            if STAGE_ORDER.index(restart_from) <= STAGE_ORDER.index("intake"):
                form_text = (
                    f"API name: {form.api_name}\n"
                    f"Description: {form.description}\n"
                    f"Requesting team: {form.requesting_team}\n"
                    f"Expected consumers: {form.expected_consumers}\n"
                    f"Data sensitivity: {form.data_sensitivity}"
                )
                intake = await self._run_stage(
                    "intake",
                    intake_activity,
                    AgentRequest(
                        subject=form_text,
                        attachment_refs=form.attachment_refs,
                        attachment_filenames=form.attachment_filenames,
                    ),
                )
                if intake is None:
                    restart_from = self._pending_amendment
                    continue

                self._awaiting_confirmation = True
                await workflow.wait_condition(
                    lambda: not self._awaiting_confirmation or self._pending_amendment is not None
                )
                if self._pending_amendment is not None:
                    self._awaiting_confirmation = False
                    restart_from = self._pending_amendment
                    continue

            if STAGE_ORDER.index(restart_from) <= STAGE_ORDER.index("triage"):
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
                self._transcript.append(
                    TranscriptEntry("risk_scoring", "output", risk_response.output)
                )
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
                if triage is None:
                    restart_from = self._pending_amendment
                    continue

            architecture = await self._run_stage(
                "architecture_evaluator",
                architecture_evaluator_activity,
                AgentRequest(subject=intake.output, context=form.architecture_notes),
            )
            if architecture is None:
                restart_from = self._pending_amendment
                continue

            if self._pending_amendment is None:
                break
            restart_from = self._pending_amendment

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

    @workflow.update
    async def confirm_intake_summary(self) -> str:
        """Approves the just-produced intake summary, letting the pipeline
        proceed into triage. Call submit_amendment instead to correct a
        field — that re-runs intake and lands back on this same checkpoint."""
        self._awaiting_confirmation = False
        return "confirmed"

    @confirm_intake_summary.validator
    def _validate_confirm_intake_summary(self) -> None:
        if not self._awaiting_confirmation:
            raise ValueError("No intake summary is currently awaiting confirmation.")

    @workflow.update
    async def submit_amendment(self, field: str, value: str) -> str:
        """Corrects a field the user already submitted, even if a later
        stage has already run (or is currently paused asking its own
        question). Takes effect at the next stage checkpoint: everything
        from the affected stage onward gets discarded and re-run with the
        corrected value — including re-asking any clarification those
        stages already resolved."""
        setattr(self._form, field, value)
        restart_stage = AMENDABLE_FIELDS[field]
        if self._pending_amendment is None or STAGE_ORDER.index(restart_stage) < STAGE_ORDER.index(
            self._pending_amendment
        ):
            self._pending_amendment = restart_stage
        self._transcript.append(TranscriptEntry("user", "answer", f"[Amended {field}]: {value}"))
        return "accepted"

    @submit_amendment.validator
    def _validate_submit_amendment(self, field: str, value: str) -> None:
        if self._done:
            raise ValueError("Workflow has already completed; amendments are no longer accepted.")
        if field not in AMENDABLE_FIELDS:
            raise ValueError(f"'{field}' is not amendable. Valid fields: {sorted(AMENDABLE_FIELDS)}")
        if not value or not value.strip():
            raise ValueError("Value cannot be blank.")

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
            awaiting_confirmation=self._awaiting_confirmation,
        )
