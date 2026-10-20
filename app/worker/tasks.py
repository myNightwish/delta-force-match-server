"""arq 异步任务：OCR 战绩截图识别。

当前为桩：返回真实截图模板字段（钻石II ★4 4418分 等）。
生产 TODO: 按版式做模板定位，
调用腾讯云/阿里云 OCR，字段交叉校验后返回。
"""
import asyncio
import uuid

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import settings

# 真实截图模板值（演示/联调用）
TEMPLATE_FIELDS = {
    "game_uid": "",
    "game_name": "",
    "mode": "烽火地带",
    "tier_name": "钻石II",
    "tier_stars": 4,
    "tier_score": 4418,
    "matches": 1673,
    "play_hours": 332.0,
    "radar_combat": 59,
    "radar_survival": 77,
    "radar_coop": 56,
    "radar_search": 64,
    "radar_wealth": 91,
    "profit_ratio": "1.1M",
    "extract_value": "1.1B",
    "kda_k": 1.0,
    "kda_d": 1.5,
    "kda_a": 0.9,
    "kills": 1258,
    "hit_rate": 34.0,
    "precise_kill_rate": 63.3,
    "extract_rate": 40.4,
    "teammate_extract_value": "22.7M",
    "rescues": 160,
    "revives": 114,
    "season": "",
    "ocr_confidence": 0.97,
}


async def ocr_parse(ctx: dict, file_path: str) -> dict:
    # TODO: 真实 OCR：读图 -> 版式定位 -> 云 OCR -> 字段校验
    await asyncio.sleep(2)  # 模拟识别耗时
    return {"file_path": file_path, "fields": dict(TEMPLATE_FIELDS)}


def ocr_parse_sync() -> dict:
    """本地联调：不走 Redis/arq，直接返回模板字段。"""
    return dict(TEMPLATE_FIELDS)


_SYNC_RESULTS: dict[str, dict] = {}


def enqueue_ocr_sync(file_path: str) -> str:
    """Redis 不可用时的降级：同步返回模板字段，job 立即可查。"""
    job_id = "sync_" + uuid.uuid4().hex
    _SYNC_RESULTS[job_id] = {"file_path": file_path, "fields": dict(TEMPLATE_FIELDS)}
    return job_id


def fetch_ocr_sync(job_id: str) -> dict | None:
    if job_id in _SYNC_RESULTS:
        return {"status": "done", "result": _SYNC_RESULTS[job_id]}
    return None


async def enqueue_ocr(file_path: str) -> str:
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        job = await pool.enqueue_job("ocr_parse", file_path)
        return job.job_id
    finally:
        await pool.close()


async def fetch_ocr_result(job_id: str) -> dict | None:
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        job = await pool.get_job(job_id)
        info = await job.info()
        if info is None or info.status is None:
            return {"status": "pending"}
        status = str(info.status)
        if status == "complete":
            return {"status": "done", "result": info.result}
        if status == "failed":
            return {"status": "failed"}
        return {"status": "pending"}
    finally:
        await pool.close()


class WorkerSettings:
    functions = [ocr_parse]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
