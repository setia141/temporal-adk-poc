"""Tools available to the triage classification agent."""


async def lookup_team_review_capacity(team_name: str) -> dict:
    """Looks up the reviewing team's current queue load, so a routing
    decision (Fast-track / Standard Review / Escalate) accounts for real
    capacity instead of just the request's own risk/complexity signals.

    Args:
        team_name: The team that would review this request (e.g. the
            architecture board), not the requesting team.
    """
    # ponytail: stubbed review-queue lookup — replace with a real
    # ticketing/queue API call once one is available.
    return {
        "team_name": team_name,
        "open_reviews": 0,
        "avg_review_days": 3,
    }
