from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.database import init_db
from app.services.scheduler import start_scheduler, shutdown_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    start_scheduler()
    print("Sports Predictor AI started!")
    print("Docs: http://localhost:8000/docs")
    yield
    # Shutdown
    shutdown_scheduler()


app = FastAPI(
    title="Sports Predictor AI",
    description="AI-powered sports betting analysis using Claude",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
