"""Tools available to the risk scoring agent."""


async def lookup_prior_incidents(api_name: str) -> dict:
    """Looks up prior security/compliance incidents involving this API name
    or a closely related one, to ground the risk score in real history
    instead of guessing from the intake text alone.

    Args:
        api_name: The API's name, as given on the intake form.
    """
    # ponytail: stubbed incident-history lookup — replace with a real
    # compliance/incident-tracking API call once one is available.
    return {
        "api_name": api_name,
        "prior_incidents": 0,
        "open_compliance_findings": 0,
    }
