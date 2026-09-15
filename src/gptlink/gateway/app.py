"""Gateway application factory and database/heartbeat lifecycle."""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from gptlink.common.config import Settings
from gptlink.gateway.health import router as health_router
from gptlink.gateway.registry import ConnectionRegistry
from gptlink.gateway.websocket import router as websocket_router
from gptlink.persistence.database import Database


def create_app(settings: Settings, *, database: Database | None = None) -> FastAPI:
    database = database if database is not None else Database(settings.database_url)
    registry = ConnectionRegistry(database)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(min(settings.heartbeat_interval, settings.offline_threshold))
            await registry.expire(settings.offline_threshold)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = None
        try:
            await database.init()
            task = asyncio.create_task(monitor())
            app.state.ready = True
            yield
        finally:
            app.state.ready = False
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            try:
                await registry.close()
            finally:
                await database.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.registry = registry
    app.state.ready = False
    app.include_router(health_router)
    app.include_router(websocket_router)
    return app
