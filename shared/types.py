from dataclasses import dataclass, field


@dataclass
class IntakeForm:
    api_name: str
    description: str
    requesting_team: str
    expected_consumers: str
    data_sensitivity: str  # "None" | "Internal only" | "PII" | "Confidential/Regulated"
    architecture_notes: str = ""
    # Optional supporting files (PDF, image, text/markdown, ...). Each ref is
    # an opaque key resolved via attachment_store.get_attachment_store() —
    # never a client-local path, since the workflow may run on a different
    # host than the client. Parallel lists, same length, same order.
    attachment_refs: list[str] = field(default_factory=list)
    attachment_filenames: list[str] = field(default_factory=list)


@dataclass
class AgentRequest:
    subject: str
    context: str = ""
    attachment_refs: list[str] = field(default_factory=list)
    attachment_filenames: list[str] = field(default_factory=list)


@dataclass
class AgentResponse:
    agent_name: str
    output: str
    needs_clarification: bool = False
    question: str = ""


@dataclass
class IntakeResult:
    canonical_intake: str
    risk_score: str
    complexity_assessment: str
    classification: str
    architecture_review: str


@dataclass
class TranscriptEntry:
    agent_name: str
    role: str  # "output" | "question" | "answer"
    text: str


@dataclass
class IntakeStatus:
    summary: str
    stage: str  # "intake" | "triage" | "architecture_evaluator" | "done"
    waiting_for_input: bool
    pending_question: str
    transcript: list[TranscriptEntry] = field(default_factory=list)
    is_complete: bool = False
    result: IntakeResult | None = None
    # True right after intake produces its canonical summary, before triage
    # starts — the user must confirm_intake_summary (or amend a field, which
    # re-runs intake) to proceed. Distinct from waiting_for_input, which is
    # the agent itself asking a question; this is a workflow-level review
    # checkpoint the agent doesn't know about.
    awaiting_confirmation: bool = False
