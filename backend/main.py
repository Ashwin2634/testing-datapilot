from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os

from app.core.config import settings
from app.core.database import init_db
from app.api.jobs import router as jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    print("[OK] Database initialized")
    print(f"[OK] Upload dir: {settings.UPLOAD_DIR}")
    print(f"[OK] Output dir: {settings.OUTPUT_DIR}")
    yield
    # Shutdown
    print("[OK] Shutting down...")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins="*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Routers
app.include_router(jobs_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    return {
        "message": "QC Product Processor API is running",
        "docs": "/docs",
        "version": "0.1.0",
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)