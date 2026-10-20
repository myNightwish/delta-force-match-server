from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.security import create_access_token, send_sms_code, verify_sms_code
from app.models import User
from app.schemas.auth import LoginIn, LoginOut, SendCodeIn, SendCodeOut

router = APIRouter()


@router.post("/auth/code", response_model=SendCodeOut)
def send_code(data: SendCodeIn, db: Session = Depends(get_db)):
    try:
        result = send_sms_code(data.phone)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    return SendCodeOut(ok=True, dev_code=result.get("dev_code"))


@router.post("/auth/login", response_model=LoginOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    if not verify_sms_code(data.phone, data.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    user = db.query(User).filter(User.phone == data.phone).first()
    is_new = False
    if user is None:
        user = User(phone=data.phone, nickname=f"岛民{data.phone[-4:]}")
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new = True
    token = create_access_token(user.id)
    return LoginOut(token=token, is_new_user=is_new, user_id=user.id)
