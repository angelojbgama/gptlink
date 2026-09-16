"""Gateway application factory and database/heartbeat lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from gptlink.common.config import Settings
from gptlink.gateway.health import router as health_router
from gptlink.gateway.operations import GatewayOperations
from gptlink.gateway.pairing_api import router as pairing_router
from gptlink.gateway.registry import ConnectionRegistry
from gptlink.gateway.remote import RemoteAgentOperations
from gptlink.gateway.websocket import router as websocket_router
from gptlink.mcp.auth import BearerAuthenticator, BearerAuthMiddleware
from gptlink.mcp.server import create_mcp_server
from gptlink.persistence.database import Database

logger = logging.getLogger(__name__)


def create_app(settings: Settings, *, database: Database | None = None) -> FastAPI:
    database = database if database is not None else Database(settings.database_url)
    registry = ConnectionRegistry(database)
    operations = GatewayOperations(database)
    remote = RemoteAgentOperations(registry)
    mcp_server = create_mcp_server(operations, remote, caller=settings.mcp_caller)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(min(settings.heartbeat_interval, settings.offline_threshold))
            try:
                await database.check()
                await registry.expire(settings.offline_threshold)
            except Exception as error:
                app.state.ready = False
                # Exception text can contain SQL parameters; only log its class.
                logger.warning("Gateway monitor cycle failed (%s)", type(error).__name__)
            else:
                app.state.ready = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        try:
            await database.init()
            async with mcp_server.session_manager.run():
                task = asyncio.create_task(monitor())
                app.state.ready = True
                yield
        finally:
            app.state.ready = False
            try:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            finally:
                try:
                    await registry.close()
                finally:
                    await database.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.registry = registry
    app.state.operations = operations
    app.state.remote_operations = remote
    app.state.mcp_server = mcp_server
    app.state.ready = False
    app.include_router(health_router)
    app.include_router(pairing_router)
    app.include_router(websocket_router)
    mcp_app = mcp_server.streamable_http_app(
        stateless_http=True,
        host=settings.gateway_host,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )
    token = settings.mcp_token.get_secret_value() if settings.mcp_token is not None else None
    app.mount(
        "/",
        BearerAuthMiddleware(
            mcp_app,
            BearerAuthenticator(token, caller=settings.mcp_caller),
        ),
        name="mcp",
    )
    return app
