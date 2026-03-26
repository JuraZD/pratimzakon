import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .limiter import limiter
from dotenv import load_dotenv

from .database import engine, Base
from .routers import auth, keywords, stripe_router, admin, search, stats
from .migrate_db import run_migrations

load_dotenv()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pokrenemo migracije kao background task — yield odmah da Render detektira port
    import asyncio

    async def _run_db_setup():
        try:
            await asyncio.to_thread(lambda: Base.metadata.create_all(bind=engine))
            await asyncio.to_thread(run_migrations)
        except Exception as e:
            logger.error(f"DB startup error: {e}")

    asyncio.create_task(_run_db_setup())
    yield


app = FastAPI(title="PratimZakon API", version="1.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost")

# Podržava više origina odvojenih zarezom u FRONTEND_URL env varijabli
# Npr: FRONTEND_URL=https://jurazd.github.io,https://pratimzakon.hr
_extra_origins = [o.strip() for o in FRONTEND_URL.split(",") if o.strip()]
ALLOWED_ORIGINS = _extra_origins + [
    "http://localhost:3000",
    "http://localhost:5500",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(keywords.router)
app.include_router(stripe_router.router)
app.include_router(admin.router)
app.include_router(search.router)
app.include_router(stats.router)


@app.get("/health")
def health():
    return {"status": "ok"}
