"""Tools available to the intake preparation agent."""


async def lookup_requesting_team(team_name: str) -> dict:
    """Looks up org info for a requesting team: cost center, on-call channel,
    and whether they've onboarded an API before.

    Args:
        team_name: The requesting team's name, as given on the intake form.
    """
    # ponytail: stubbed org-directory lookup — replace with a real internal
    # directory/HR API call once one is available.
    return {
        "team_name": team_name,
        "cost_center": "UNKNOWN",
        "oncall_channel": "#platform-oncall",
        "prior_api_onboarded": False,
    }
