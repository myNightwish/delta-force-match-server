from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routers import pilot
from app.core.config import settings
from app.services.pilot import VERSION

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENABLE_LEGACY_API:
        from app.db.session import init_db
        init_db()
    yield

app = FastAPI(title='river-delta-api', version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip().rstrip('/') for origin in settings.CORS_ORIGINS.split(',') if origin.strip()],
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type'],
)
app.include_router(pilot.router, prefix='/api')

# The legacy SMS/OCR/payment stubs are not exposed by the public matching service.
if settings.ENABLE_LEGACY_API:
    from app.api.routers import auth, chat, island, match, ocr, pay, profile, rating
    for router in [auth, chat, island, match, ocr, pay, profile, rating]:
        app.include_router(router.router, prefix='/api')

@app.get('/health')
def health():
    return {'ok': True, 'version': VERSION}
