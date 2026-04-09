import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse

from app.core.config import settings
from app.core.database import init_db
from app.api.customer import router as customer_router
from app.api.admin import router as admin_router
from app.api.admin_views import router as admin_views_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting reklamace_proteinaco...")
    init_db()
    # Ensure upload directory exists
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    yield
    logger.info("Shutting down reklamace_proteinaco...")


app = FastAPI(
    title="Reklamace Proteinaco",
    description="Returns and complaints management for proteinaco.cz",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
_default_cors = ",".join([
    "http://localhost:8000",
    "http://localhost:8040",
    "https://admin.proteinaco.com",
    "https://reklamace.proteinaco.com",
    "https://www.proteinaco.cz",
    "https://www.proteinaco.sk",
    "https://www.proteinaco.hu",
    "https://www.proteinaco.pl",
    "https://www.proteinaco.ro",
])
_cors_origins = os.getenv("CORS_ORIGINS", _default_cors).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=True,
)

# Static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


app.include_router(customer_router)
app.include_router(admin_router)
app.include_router(admin_views_router)


@app.get("/reklamace-test", response_class=HTMLResponse)
async def reklamace_test():
    """Serve the customer-facing widget for testing."""
    widget_path = Path(__file__).parent.parent / "shoptet_widget" / "reklamace-widget.html"
    return HTMLResponse(
        content=widget_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "reklamace_proteinaco"}
