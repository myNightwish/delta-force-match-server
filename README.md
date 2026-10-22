> 当前产品入口为两人招募匹配 API；下文历史架构介绍包含旧演示模块，当前部署与功能请以 [部署说明](DEPLOY_RENDER.md) 和 [当前功能说明](docs/growth-features.md) 为准

# 三角洲组队匹配 · 服务端 (MVP)

FastAPI + SQLAlchemy 2.0 + 自建 PostgreSQL，按《服务端详细设计文档 v1.2》实现的
可运行 MVP 骨架：登录、档案、OCR（桩）、岛屿浏览、组队撮合、¥1 匹配费（桩）、
80% 退款、WebSocket 聊天、赛后评价，全流程可跑通。

## 快速启动

```bash
# 1. 本地快速跑（SQLite，零依赖）
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
# 接口文档: http://127.0.0.1:8000/docs

# 2. Docker 全套（PostgreSQL + Redis + API + Worker）
docker compose up --build
```

种子数据（4 个演示玩家，含真实截图模板字段）：

```bash
python -m app.seed
```

## 核心流程（MVP）

1. `POST /api/auth/code` 发验证码（开发模式直接返回 `6688`）
2. `POST /api/auth/login` 验码登录 → JWT（30 天）
3. `POST /api/ocr/screenshot` 上传战绩截图 → 异步识别 →
   `GET /api/ocr/result/{job_id}` → `POST /api/ocr/confirm` 写入档案
4. `GET /api/island/players?island=tech` 免费浏览（KD 对未入队用户隐藏）
5. `POST /api/posts` 建房（招 1 人 / 招 2 人）→ 房主先付 ¥1
6. `POST /api/posts/{id}/apply` 申请 → 房主 `accept` → 申请者付 ¥1 → 入队
7. 入队后 `GET /api/matches/{id}/team` 可见全员 KD
8. 全员 `POST /api/matches/{id}/confirm` → 锁定，交易完成，不退款
9. 锁定前 `POST /api/matches/{id}/quit` 可退出，退 80%（¥0.8）
10. 锁定后 `WS /api/ws/chat/{match_id}` 聊天 + `POST /api/matches/{id}/room-code` 发房间码
11. `POST /api/matches/{id}/rate` 赛后互评 → 信誉分

## 桩（STUB）与 TODO

- `app/core/security.py`：短信验证码为开发桩，`SMS_DEV_MODE=true` 时直接返回固定码。
  TODO: 接入腾讯云/阿里云短信。
- `app/worker/tasks.py`：OCR 为桩，返回真实截图模板字段。
  TODO: 接入腾讯云/阿里云 OCR，按版式做模板定位。
- `app/api/routers/pay.py`：微信支付为桩，订单/回调均为模拟。
  TODO: 企业商户号 + 微信支付 v3 API + 回调验签。
- 聊天为单机内存 WS 管理器，多实例部署需换 Redis Pub/Sub。

## 目录

```
app/
  main.py            应用入口与路由挂载
  core/              配置、JWT、短信桩
  db/                Base、Session
  models/            7 张表：users / player_profiles / match_posts /
                     match_applications / team_matches / team_members /
                     chat_messages / payments / refunds / ratings
  schemas/           Pydantic v2 入参出参
  api/routers/       auth / profile / ocr / pay / island / match / chat / rating
  services/          matching.py（匹配分）/ billing.py（80% 退款）
  worker/            arq 异步任务（OCR）
  seed.py            演示数据
```
