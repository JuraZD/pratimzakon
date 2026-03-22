import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

from .database import engine, Base
from .routers import auth, keywords, stripe_router, admin
from .migrate_db import run_migrations

load_dotenv()

Base.metadata.create_all(bind=engine)
run_migrations()

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="PratimZakon API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "http://localhost:5500"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(keywords.router)
app.include_router(stripe_router.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {"status": "ok"}
