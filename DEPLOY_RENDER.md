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
