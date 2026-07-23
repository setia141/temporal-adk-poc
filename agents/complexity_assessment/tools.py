"""Tools available to the complexity assessment agent."""


async def lookup_downstream_dependencies(api_name: str) -> dict:
    """Looks up how many existing systems would need to integrate with or be
    migrated for this API, to ground the complexity rating in the real
    dependency graph instead of guessing from the intake text alone.

    Args:
        api_name: The API's name, as given on the intake form.
    """
    # ponytail: stubbed dependency-graph lookup — replace with a real
    # service-catalog/dependency-graph API call once one is available.
    return {
        "api_name": api_name,
        "downstream_dependents": 0,
        "requires_migration": False,
    }
