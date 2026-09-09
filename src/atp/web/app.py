"""Importable local-only HTTP adapter. No listener, session, or startup operation."""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from atp.web.model import WebError, WebReasonCode
from atp.web.projections import ROUTES, project


def create_app(state: object) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)

    def endpoint(resource: str) -> Callable[[Request], Awaitable[Response]]:
        async def handle(request: Request) -> Response:
            result = project(state, resource)
            code = 200
            if isinstance(result, WebError):
                code = 404 if result.reason_code is WebReasonCode.ARTIFACT_NOT_AVAILABLE else 409
            response = JSONResponse(
                result.model_dump(mode="json", exclude_none=False), status_code=code
            )
            if request.method == "HEAD":
                return Response(status_code=code, headers=dict(response.headers))
            return response

        return handle

    for route in ROUTES:
        app.add_api_route(route, endpoint(route), methods=["GET", "HEAD"], response_model=None)
    return app
