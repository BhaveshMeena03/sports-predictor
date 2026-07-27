import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.database import init_db
from app.core import payments
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

# x402 payment middleware. No-ops unless X402_PAY_TO is set, so an
# unconfigured deploy serves the slate free rather than half-paywalled.
payments.install(app)

app.include_router(router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    # reload= and the bind address are env-driven: reload watches the filesystem
    # and must never be on in production, and binding 0.0.0.0 by default would
    # expose a dev server to the whole network.
    # Proxy headers only when TRUST_PROXY says we are actually behind one.
    # X-Forwarded-Proto is spoofable by any client, so trusting it on a
    # directly-exposed server would let a caller dictate the scheme the app
    # reports — including the resource URL x402 puts in its payment challenge.
    behind_proxy = os.getenv("TRUST_PROXY") == "1"
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEV_RELOAD") == "1",
        proxy_headers=behind_proxy,
        forwarded_allow_ips="*" if behind_proxy else None,
    )
