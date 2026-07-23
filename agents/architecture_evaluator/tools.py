"""Tools available to the architecture evaluator agent.

Activity + wrapper pair — see agents/intake/tools.py for why tools must
route through their own Temporal activity on this branch.
"""

import logging
from datetime import timedelta

from temporalio import activity, workflow

logger = logging.getLogger(__name__)

# Invented-but-plausible baseline standards doc for the evaluator to compare
# against — swap for a real org standard when this stops being a POC.
ARCHITECTURE_STANDARDS = """
- APIs must be exposed as versioned REST endpoints (e.g. /v1/...) or gRPC
  services with proto-defined contracts. No unversioned or ad-hoc endpoints.
- All external-facing APIs must authenticate callers via OAuth2
  client-credentials or mTLS.
- Any API handling PII or confidential/regulated data must encrypt data at
  rest and in transit, and log access for audit purposes.
- Services must be stateless and horizontally scalable; any session state
  must live in an external store, not in-process memory.
- New APIs should reuse the shared API gateway and rate-limiting
  infrastructure rather than standing up bespoke gateways.
"""


@activity.defn
async def fetch_architecture_standards_activity() -> str:
    logger.info("fetch_architecture_standards called")
    # ponytail: stubbed standards doc — replace with a real internal
    # wiki/standards-service API call once one is available.
    return ARCHITECTURE_STANDARDS


async def fetch_architecture_standards() -> str:
    """Fetches the current org architecture standards doc to compare the
    submitted architecture notes against. Always call this before evaluating
    — standards can change, so don't rely on prior knowledge of them.
    """
    return await workflow.execute_activity(
        fetch_architecture_standards_activity,
        start_to_close_timeout=timedelta(seconds=30),
    )
