import asyncio
import os
import uuid

import streamlit as st
from dotenv import load_dotenv
from temporalio.client import Client

from workflow import ContentPipelineWorkflow

load_dotenv()

st.set_page_config(page_title="Temporal ADK Content Pipeline", page_icon="📝")
st.title("Temporal ADK Content Pipeline")
st.caption("Researcher → Writer → Reviewer, orchestrated as Temporal activities.")


async def run_workflow(topic: str):
    address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    task_queue = os.environ.get("TEMPORAL_TASK_QUEUE", "adk-agents-task-queue")

    client = await Client.connect(address, namespace=namespace)
    workflow_id = f"content-pipeline-{uuid.uuid4()}"
    return await client.execute_workflow(
        ContentPipelineWorkflow.run,
        topic,
        id=workflow_id,
        task_queue=task_queue,
    )


topic = st.text_input(
    "Topic",
    value="Why Temporal is a good fit for orchestrating LLM agent pipelines",
)

if st.button("Run pipeline", type="primary"):
    if not topic.strip():
        st.warning("Enter a topic first.")
    else:
        with st.spinner("Running researcher → writer → reviewer..."):
            try:
                result = asyncio.run(run_workflow(topic))
            except Exception as exc:
                st.error(
                    "Workflow failed. Is the Temporal server running "
                    "(`temporal server start-dev`) and is `worker.py` running "
                    f"and polling the task queue?\n\n{exc}"
                )
            else:
                st.subheader("Research")
                st.markdown(result.research)
                st.subheader("Draft")
                st.markdown(result.draft)
                st.subheader("Review")
                st.markdown(result.review)
