"""Route inspection for tests.

FastAPI 0.141 defers router inclusion: ``app.routes`` holds a wrapper object
for each included router instead of that router's routes. These helpers walk
the wrappers so tests can see every route, as they could before.
"""


def iter_routes(routes):
    """Yield every route, descending into included routers."""
    for route in routes:
        router = getattr(route, "original_router", None)
        if router is not None:
            yield from iter_routes(router.routes)
        else:
            yield route


def app_route_map(app) -> dict:
    """Map each route path in the app to its route."""
    return {r.path: r for r in iter_routes(app.routes) if hasattr(r, "path")}
