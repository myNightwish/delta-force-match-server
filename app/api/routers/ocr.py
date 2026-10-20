import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models import User
from app.schemas.profile import ProfileIn, ProfileOut
from app.worker.tasks import enqueue_ocr, fetch_ocr_result, enqueue_ocr_sync, fetch_ocr_sync

router = APIRouter()


@router.post("/ocr/screenshot")
async def upload_screenshot(
    file: UploadFile = File(...),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传战绩截图，异步识别。版式标准：角色信息-详细数据-烽火地带。"""
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    name = f"{me.id}_{uuid.uuid4().hex}{ext}"
    path = os.path.join(settings.UPLOAD_DIR, name)
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        job_id = await enqueue_ocr(path)
    except Exception:
        # Redis 不可用时降级为同步桩（本地联调），job 立即可查
        job_id = enqueue_ocr_sync(path)
    return {"job_id": job_id}


@router.get("/ocr/result/{job_id}")
async def ocr_result(job_id: str, me: User = Depends(get_current_user)):
    if job_id.startswith("sync_"):
        r = fetch_ocr_sync(job_id)
        if r is not None:
            return r
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        return await fetch_ocr_result(job_id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"识别队列不可用: {e}")


@router.post("/ocr/confirm", response_model=ProfileOut)
def confirm_ocr(
    data: ProfileIn,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户确认识别结果后写入档案（上传一次即为个人档案）。"""
    from app.api.routers.profile import upsert_profile

    return upsert_profile(data, me, db)
