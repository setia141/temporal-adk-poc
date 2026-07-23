"""Tools available to the architecture evaluator agent."""

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


async def fetch_architecture_standards() -> str:
    """Fetches the current org architecture standards doc to compare the
    submitted architecture notes against. Always call this before evaluating
    — standards can change, so don't rely on prior knowledge of them.
    """
    # ponytail: stubbed standards doc — replace with a real internal
    # wiki/standards-service API call once one is available.
    return ARCHITECTURE_STANDARDS
