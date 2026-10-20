"""队伍聊天：WebSocket，锁定后可用。

单机内存连接管理（MVP）。多实例部署 TODO: 换 Redis Pub/Sub。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.security import decode_access_token
from app.models import ChatMessage, TeamMatch, TeamMember, User
from app.schemas.chat import MessageOut

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[int, set[WebSocket]] = {}

    async def connect(self, match_id: int, ws: WebSocket) -> None:
        await ws.accept()
        self.rooms.setdefault(match_id, set()).add(ws)

    def disconnect(self, match_id: int, ws: WebSocket) -> None:
        self.rooms.get(match_id, set()).discard(ws)

    async def broadcast(self, match_id: int, payload: dict) -> None:
        for ws in list(self.rooms.get(match_id, set())):
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(match_id, ws)


manager = ConnectionManager()


@router.websocket("/ws/chat/{match_id}")
async def chat_ws(match_id: int, websocket: WebSocket, token: str = Query(...)):
    from app.db.session import SessionLocal

    user_id = decode_access_token(token)
    if user_id is None:
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    try:
        match = db.get(TeamMatch, match_id)
        member = (
            db.query(TeamMember)
            .filter(TeamMember.match_id == match_id, TeamMember.user_id == user_id)
            .first()
        )
        if not match or not member or match.status != "locked":
            await websocket.close(code=4403)
            return
        await manager.connect(match_id, websocket)
        try:
            while True:
                data = await websocket.receive_json()
                content = str(data.get("content", ""))[:2000]
                if not content.strip():
                    continue
                msg = ChatMessage(match_id=match_id, sender_id=user_id, content=content, kind="text")
                db.add(msg)
                db.commit()
                db.refresh(msg)
                await manager.broadcast(
                    match_id,
                    {
                        "id": msg.id,
                        "sender_id": user_id,
                        "content": msg.content,
                        "kind": msg.kind,
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    },
                )
        except WebSocketDisconnect:
            manager.disconnect(match_id, websocket)
    finally:
        db.close()


@router.get("/matches/{match_id}/messages", response_model=list[MessageOut])
def message_history(
    match_id: int,
    limit: int = 50,
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.get(TeamMatch, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="队伍不存在")
    member = (
        db.query(TeamMember)
        .filter(TeamMember.match_id == match_id, TeamMember.user_id == me.id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=403, detail="你不在这支队伍中")
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.match_id == match_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(msgs))
