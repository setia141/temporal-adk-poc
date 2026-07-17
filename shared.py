from dataclasses import dataclass, field


@dataclass
class IntakeForm:
    api_name: str
    description: str
    requesting_team: str
    expected_consumers: str
    data_sensitivity: str  # "None" | "Internal only" | "PII" | "Confidential/Regulated"
    architecture_notes: str = ""


@dataclass
class AgentRequest:
    subject: str
    context: str = ""


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
