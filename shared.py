from dataclasses import dataclass


@dataclass
class AgentRequest:
    topic: str
    context: str = ""


@dataclass
class AgentResponse:
    agent_name: str
    output: str


@dataclass
class PipelineResult:
    topic: str
    research: str
    draft: str
    review: str
