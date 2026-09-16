"""MCP caller authentication, separate from Agent device credentials."""

import secrets
from typing import Protocol


class CallerAuthenticator(Protocol):
    def authenticate(self, authorization: str | None) -> str | None: ...


class BearerAuthenticator:
    def __init__(self, token: str | None, *, caller: str) -> None:
        self._token = token
        self._caller = caller

    def authenticate(self, authorization: str | None) -> str | None:
        if self._token is None or authorization is None:
            return None
        scheme, separator, credential = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not credential:
            return None
        if not secrets.compare_digest(credential, self._token):
            return None
        return self._caller


class BearerAuthMiddleware:
    def __init__(self, app, authenticator: CallerAuthenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw = headers.get(b"authorization")
        authorization = raw.decode("latin-1") if raw is not None else None
        if self.authenticator.authenticate(authorization) is None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"Unauthorized"}',
                }
            )
            return
        # Authentication is complete; do not propagate the bearer into SDK
        # internals where future diagnostics could accidentally retain it.
        authenticated_scope = dict(scope)
        authenticated_scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() != b"authorization"
        ]
        await self.app(authenticated_scope, receive, send)
