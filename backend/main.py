import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.database import init_db
from app.core.security import ADMIN_TOKEN, cors_origins
from app.services.scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    start_scheduler()
    print("Sports Predictor AI started!")
    print("Docs: http://localhost:8000/docs")
    if not ADMIN_TOKEN:
        # Loud on purpose: without a token the admin endpoints answer only to
        # loopback, so a remote deploy that skipped this will look "broken"
        # until someone reads the logs.
        print("WARNING: ADMIN_TOKEN is not set — admin endpoints are "
              "restricted to localhost. Set it to enable remote admin access.")
    yield
    # Shutdown
    shutdown_scheduler()


app = FastAPI(
    title="Sports Predictor AI",
    description="AI-powered sports betting analysis",
    version="1.0.0",
    lifespan=lifespan,
)

# An explicit allowlist, never "*". The previous config paired a wildcard with
# allow_credentials=True — a combination browsers reject, and one that invites
# any site to call this API with the visitor's credentials attached.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)

app.include_router(router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    # reload= and the bind address are env-driven: reload watches the filesystem
    # and must never be on in production, and binding 0.0.0.0 by default would
    # expose a dev server to the whole network.
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEV_RELOAD") == "1",
    )
