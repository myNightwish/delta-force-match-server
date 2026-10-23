from contextlib import asynccontextmanager, suppress
import asyncio
import logging
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
    async def simulated_peers():
        from app.services.simulation import Simulation
        while True:
            try:await asyncio.to_thread(Simulation(pilot.service).tick)
            except Exception:logging.getLogger(__name__).exception('模拟测试后台循环失败')
            await asyncio.sleep(2)
    task=asyncio.create_task(simulated_peers())
    try:yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):await task

if settings.APP_ENV=='production' and settings.ENABLE_LEGACY_API:
    raise RuntimeError('生产环境禁止开放旧版演示接口')

app = FastAPI(docs_url=None if settings.APP_ENV=='production' else '/docs', redoc_url=None if settings.APP_ENV=='production' else '/redoc', openapi_url=None if settings.APP_ENV=='production' else '/openapi.json', title='river-delta-api', version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip().rstrip('/') for origin in settings.CORS_ORIGINS.split(',') if origin.strip()],
    allow_credentials=False,
    allow_methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type'],
)
from app.services.http_security import HttpSafety
app.add_middleware(HttpSafety)

# Add codes without changing legacy detail payloads, status codes or retry headers.
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from app.api.error_codes import error_code
import json

@app.exception_handler(StarletteHTTPException)
async def coded_http_error(request, exc):
    response = await http_exception_handler(request, exc)
    if request.url.path.startswith('/api/pilot/'):
        body = json.loads(response.body)
        body['error_code'] = error_code(exc.status_code, exc.detail)
        response.body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        response.headers['content-length'] = str(len(response.body))
    return response

@app.exception_handler(RequestValidationError)
async def coded_validation_error(request, exc):
    response = await request_validation_exception_handler(request, exc)
    if request.url.path.startswith('/api/pilot/'):
        body = json.loads(response.body)
        body['error_code'] = 'message.validation_failed'
        response.body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        response.headers['content-length'] = str(len(response.body))
    return response

app.include_router(pilot.router, prefix='/api')

# The legacy SMS/OCR/payment stubs are not exposed by the public matching service.
if settings.ENABLE_LEGACY_API:
    from app.api.routers import auth, chat, island, match, ocr, pay, profile, rating
    for router in [auth, chat, island, match, ocr, pay, profile, rating]:
        app.include_router(router.router, prefix='/api')

@app.get('/health')
def health():
    return {'ok': True, 'version': VERSION}
