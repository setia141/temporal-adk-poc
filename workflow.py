from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import research_activity, write_activity, review_activity
    from shared import AgentRequest, PipelineResult

DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
)


@workflow.defn
class ContentPipelineWorkflow:
    """Orchestrates three ADK agents (research -> write -> review) as
    independently retried Temporal activities."""

    @workflow.run
    async def run(self, topic: str) -> PipelineResult:
        research = await workflow.execute_activity(
            research_activity,
            AgentRequest(topic=topic),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        draft = await workflow.execute_activity(
            write_activity,
            AgentRequest(topic=topic, context=research.output),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        review = await workflow.execute_activity(
            review_activity,
            AgentRequest(topic=topic, context=draft.output),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY_POLICY,
        )

        return PipelineResult(
            topic=topic,
            research=research.output,
            draft=draft.output,
            review=review.output,
        )
