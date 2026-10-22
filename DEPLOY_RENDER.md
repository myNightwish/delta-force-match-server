# Render: river-delta-api

代码同步并推送后，使用 Python Web Service：

- Root Directory: 留空（此仓库本身就是后端）
- Build: `pip install -r requirements.txt`
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health Check: `/health`
- 保持单实例；当前匹配存储使用 SQLite，不是 PostgreSQL。

Environment：

```dotenv
APP_ENV=production
CORS_ORIGINS=https://river-delta-web.onrender.com
ENABLE_LEGACY_API=false
PILOT_FREE_MODE=true
PILOT_EXPERIMENT_MODE=false
```

持久磁盘方案：挂载到 `/var/data`，再设置 `PILOT_DB=/var/data/pilot.db`。
Render 持久磁盘需要支持磁盘的付费服务。未挂磁盘时不要使用此路径；可暂用 `PILOT_DB=./pilot.db` 验证，但普通文件系统的数据会在重新部署等情况下丢失。

PILOT_METRICS_TOKEN 是可选的私人统计令牌，只放服务端；未设置时线上统计接口返回403。不要填入任何 VITE_* 前端变量。

新版只挂载 `/api/pilot/*` 和 `/health`，不暴露旧短信/支付桩。部署后 `/health` 应返回 version，`/api/pilot/config` 应返回匹配配置。

本地旧数据库及【测试】玩家不会上传。线上从新库开始。

## 队伍文字与语音消息

聊天接口在 `/api/pilot/squads/{id}/messages`，语音上传/下载在对应的 `/voice` 路径。
必须是该队成员且双方已确认。当前音频与文字均存入 PILOT_DB，不需要另外的对象存储账号或语音 SDK。
使用同一个持久磁盘即可保留消息。普通临时文件系统仍可能在部署后丢失数据。语音最大1MiB、最长60秒，消息保留7天（请求时清理）。
未接入实时通话、转写或 R2。不要将 API 令牌写进公开音频 URL。

## 在线状态与身份恢复

前后端须一同更新，详情见 [在线状态与身份恢复](docs/presence-recovery.md)，不需要新增环境变量。旧数据库会自动补在线时间字段，旧招募在玩家回访前暂时隐藏。

## 最近组局与基础限流

前后端一起更新，历史聊天入口、消息编号保留与限流阈值见 [最近组局与基础限流](docs/recent-chat-and-limits.md)。无需新增环境变量，继续使用持久数据库。

## 口令保护的测试工具

后端 Environment 可设置 `PILOT_TEST_ACCESS_CODE`（至少 12 字符，留空关闭），不要放入前端变量或 Git。前后端一起更新，使用方式见 [测试工具](docs/testing-tools.md)。

## 一人模拟测试

沿用测试口令，前后端一起更新。模拟队友由 API 单进程内的后台循环运行，维持单实例与单 worker，使用方式与隔离规则见 [模拟队友](docs/simulated-peers.md)。
