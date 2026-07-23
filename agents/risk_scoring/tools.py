"""Tools available to the risk scoring agent.

Activity + wrapper pair — see agents/intake/tools.py for why tools must
route through their own Temporal activity on this branch.
"""

import logging
from datetime import timedelta

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


@activity.defn
async def lookup_prior_incidents_activity(api_name: str) -> dict:
    logger.info("lookup_prior_incidents called: api_name=%s", api_name)
    # ponytail: stubbed incident-history lookup — replace with a real
    # compliance/incident-tracking API call once one is available.
    return {
        "api_name": api_name,
        "prior_incidents": 0,
        "open_compliance_findings": 0,
    }


async def lookup_prior_incidents(api_name: str) -> dict:
    """Looks up prior security/compliance incidents involving this API name
    or a closely related one, to ground the risk score in real history
    instead of guessing from the intake text alone.

    Args:
        api_name: The API's name, as given on the intake form.
    """
    return await workflow.execute_activity(
        lookup_prior_incidents_activity,
        api_name,
        start_to_close_timeout=timedelta(seconds=30),
    )
