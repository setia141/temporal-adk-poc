"""Tools available to the triage classification agent.

Activity + wrapper pair — see agents/intake/tools.py for why tools must
route through their own Temporal activity on this branch.
"""

import logging
from datetime import timedelta

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


@activity.defn
async def lookup_team_review_capacity_activity(team_name: str) -> dict:
    logger.info("lookup_team_review_capacity called: team_name=%s", team_name)
    # ponytail: stubbed review-queue lookup — replace with a real
    # ticketing/queue API call once one is available.
    return {
        "team_name": team_name,
        "open_reviews": 0,
        "avg_review_days": 3,
    }


async def lookup_team_review_capacity(team_name: str) -> dict:
    """Looks up the reviewing team's current queue load, so a routing
    decision (Fast-track / Standard Review / Escalate) accounts for real
    capacity instead of just the request's own risk/complexity signals.

    Args:
        team_name: The team that would review this request (e.g. the
            architecture board), not the requesting team.
    """
    return await workflow.execute_activity(
        lookup_team_review_capacity_activity,
        team_name,
        start_to_close_timeout=timedelta(seconds=30),
    )
