"""Tools available to the complexity assessment agent.

Activity + wrapper pair — see agents/intake/tools.py for why tools must
route through their own Temporal activity on this branch.
"""

import logging
from datetime import timedelta

from temporalio import activity, workflow

logger = logging.getLogger(__name__)


@activity.defn
async def lookup_downstream_dependencies_activity(api_name: str) -> dict:
    logger.info("lookup_downstream_dependencies called: api_name=%s", api_name)
    # ponytail: stubbed dependency-graph lookup — replace with a real
    # service-catalog/dependency-graph API call once one is available.
    return {
        "api_name": api_name,
        "downstream_dependents": 0,
        "requires_migration": False,
    }


async def lookup_downstream_dependencies(api_name: str) -> dict:
    """Looks up how many existing systems would need to integrate with or be
    migrated for this API, to ground the complexity rating in the real
    dependency graph instead of guessing from the intake text alone.

    Args:
        api_name: The API's name, as given on the intake form.
    """
    return await workflow.execute_activity(
        lookup_downstream_dependencies_activity,
        api_name,
        start_to_close_timeout=timedelta(seconds=30),
    )
