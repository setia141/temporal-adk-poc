"""Tools available to the intake preparation agent.

Each tool is a pair: an @activity.defn holding the real work (stubbed for
now), and a same-signature wrapper that the agent actually calls. On this
branch agent code executes as workflow code (the plugin proxies only the
model call to an activity), so a tool that will eventually do real I/O must
route through its own Temporal activity to stay deterministic under replay —
the wrapper is that routing. The wrapper's docstring is what the LLM sees.
"""

import logging
from datetime import timedelta

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


@activity.defn
async def lookup_requesting_team_activity(team_name: str) -> dict:
    logger.info("lookup_requesting_team called: team_name=%s", team_name)
    # ponytail: stubbed org-directory lookup — replace with a real internal
    # directory/HR API call once one is available.
    return {
        "team_name": team_name,
        "cost_center": "UNKNOWN",
        "oncall_channel": "#platform-oncall",
        "prior_api_onboarded": False,
    }


async def lookup_requesting_team(team_name: str) -> dict:
    """Looks up org info for a requesting team: cost center, on-call channel,
    and whether they've onboarded an API before.

    Args:
        team_name: The requesting team's name, as given on the intake form.
    """
    return await workflow.execute_activity(
        lookup_requesting_team_activity,
        team_name,
        start_to_close_timeout=timedelta(seconds=30),
    )
