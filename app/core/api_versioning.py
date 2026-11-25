"""Accept-header based dispatch for versioned FastAPI applications."""

import re
from collections.abc import Callable, Mapping

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_API_VERSION = "v1"
SUPPORTED_API_VERSIONS = ("v1", "v2")
API_VERSION_MEDIA_TYPES = {
    version: f"application/vnd.sp-project.{version}+json"
    for version in SUPPORTED_API_VERSIONS
}
_MEDIA_TYPE_VERSIONS = {
    media_type: version
    for version, media_type in API_VERSION_MEDIA_TYPES.items()
}

_VERSIONED_MEDIA_TYPE = re.compile(
    r"application/vnd\.sp-project\.(v?\d+)\+json",
    re.IGNORECASE,
)


class NotAcceptableAPIRequest(ValueError):
    """The Accept header does not match a supported API representation."""


def _normalized_version(value: str) -> str:
    value = value.strip().lower()
    return value if value.startswith("v") else f"v{value}"


def _quality(parameters: list[str]) -> float:
    for parameter in parameters:
        key, separator, value = parameter.strip().partition("=")
        if separator and key.lower() == "q":
            try:
                quality = float(value.strip())
            except ValueError:
                return 0
            return quality if 0 < quality <= 1 else 0
    return 1


def parse_accept_version(
    accept_header: str | None,
    *,
    default_version: str = DEFAULT_API_VERSION,
    supported_versions: tuple[str, ...] = SUPPORTED_API_VERSIONS,
) -> str:
    """Choose the highest-priority supported version from an Accept header."""
    if not accept_header or not accept_header.strip():
        return default_version

    candidates: list[tuple[float, int, str]] = []
    for position, media_range in enumerate(accept_header.split(",")):
        media_parts = media_range.split(";")
        media_type = media_parts[0].strip().lower()
        parameters = media_parts[1:]
        quality = _quality(parameters)
        if quality == 0:
            continue

        version = None
        version_parameter = next(
            (
                value.strip().strip('"')
                for parameter in parameters
                for key, separator, value in [parameter.strip().partition("=")]
                if separator and key.lower() == "version"
            ),
            None,
        )
        if version_parameter is not None and media_type == "application/json":
            version = _normalized_version(version_parameter)
        else:
            version = _MEDIA_TYPE_VERSIONS.get(media_type)
            if version is None:
                match = _VERSIONED_MEDIA_TYPE.fullmatch(media_type)
                if match:
                    version = _normalized_version(match.group(1))

        if version is not None:
            candidates.append((quality, -position, version))
        elif media_type in ("application/json", "application/*", "*/*"):
            candidates.append((quality, -position, default_version))

    if not candidates:
        raise NotAcceptableAPIRequest(
            "Accept must include a supported versioned media type or JSON."
        )

    selected_version = max(candidates)[2]
    if selected_version not in supported_versions:
        supported = ", ".join(supported_versions)
        raise NotAcceptableAPIRequest(
            f"Unsupported API version '{selected_version}'. "
            f"Supported versions: {supported}."
        )
    return selected_version


class AcceptHeaderVersionMiddleware:
    """Dispatch requests to a version-specific ASGI app using Accept."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        apps: Mapping[str, ASGIApp],
        default_version: str = DEFAULT_API_VERSION,
        openapi_schemas: Mapping[str, Callable[[], dict]] | None = None,
    ) -> None:
        if default_version not in apps:
            raise ValueError(
                f"Default API version '{default_version}' is not configured"
            )
        self.app = app
        self.apps = apps
        self.default_version = default_version
        self.openapi_schemas = openapi_schemas or {}

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        path_version = None
        route_path = path
        for candidate in self.apps:
            prefix = f"/{candidate}"
            if path == prefix or path.startswith(f"{prefix}/"):
                path_version = candidate
                route_path = path[len(prefix) :] or "/"
                break

        for version, schema_getter in self.openapi_schemas.items():
            if path == f"/openapi/{version}.json":
                await JSONResponse(
                    content=schema_getter(),
                    headers={"X-API-Version": version, "Vary": "Accept"},
                )(
                    scope,
                    receive,
                    send,
                )
                return

        if path_version is None:
            accept_header = ", ".join(
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"accept"
            )
            try:
                version = parse_accept_version(
                    accept_header,
                    default_version=self.default_version,
                    supported_versions=tuple(self.apps),
                )
            except NotAcceptableAPIRequest as error:
                response = JSONResponse(
                    status_code=406,
                    content={
                        "detail": str(error),
                        "supported_versions": list(self.apps),
                    },
                    headers={"Vary": "Accept"},
                )
                await response(scope, receive, send)
                return
        else:
            version = path_version

        versioned_scope = dict(scope)
        if path_version is not None:
            versioned_scope["path"] = route_path
            versioned_scope["root_path"] = (
                f"{scope.get('root_path', '').rstrip('/')}/{version}"
            )
            raw_path = scope.get("raw_path", b"")
            raw_prefix = f"/{version}".encode("ascii")
            if raw_path.startswith(raw_prefix):
                versioned_scope["raw_path"] = raw_path[len(raw_prefix) :] or b"/"

        versioned_state = dict(scope.get("state", {}))
        versioned_state["api_version"] = version
        versioned_scope["state"] = versioned_state
        await self.apps[version](
            versioned_scope,
            receive,
            self._versioned_send(send, version),
        )

    @staticmethod
    def _versioned_send(send: Send, version: str) -> Send:
        async def send_with_version_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                header_names = {name.lower() for name, _ in headers}
                if b"x-api-version" not in header_names:
                    headers.append((b"x-api-version", version.encode("ascii")))
                vary_values = [
                    value.decode("latin-1")
                    for name, value in headers
                    if name.lower() == b"vary"
                ]
                if not any(
                    "accept" in {item.strip().lower() for item in value.split(",")}
                    for value in vary_values
                ):
                    headers.append((b"vary", b"Accept"))
                message = dict(message, headers=headers)
            await send(message)

        return send_with_version_headers
