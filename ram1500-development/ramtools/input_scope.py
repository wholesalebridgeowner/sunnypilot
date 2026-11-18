"""Fail-closed local log input scope for read-only Ram comfort analysis.

Segment IDs repeat between routes; comparing files from multiple routes can
create false as-of joins even if the source decoder is otherwise correct.
Only file paths are inspected here; no log data are read or identifiers emitted.
"""
from ramtools.logio import segment_of


def require_one_route(paths):
    paths = list(paths)
    if not paths:
        raise ValueError("At least one identifiable route segment is required")
    routes = set()
    for path in paths:
        route, segment = segment_of(path)
        if route is None or segment is None:
            raise ValueError("All log filenames must have a valid route and segment")
        routes.add(route)
        if len(routes) > 1:
            raise ValueError("Do not mix multiple routes in a comfort analysis")
    return paths
