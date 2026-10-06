# 邮箱验证登录

任意合法邮箱都可以申请一次性 Magic Link。邮箱验证成功后建立 Session，即可使用聊天和点赞/点踩；API 最终响应含来源，普通聊天页隐藏引用。不同邮箱的对话与反馈相互隔离。未登录直接调用聊天 API 返回 401；网页会引导到登录页。只有通过邮箱验证、且 Session 邮箱位于 `INSPECTOR_ADMIN_EMAILS` 的管理员，才能访问 RAG Inspector、Feedback 管理页面、API 文档与管理 API。非管理员访问返回 403，未登录访问返回 401。`/health` 无需登录。

## 本机使用

本机 `AUTH_DELIVERY=local` 模式只接受回环地址请求。运行 `start.bat` 后打开 `http://127.0.0.1:8000/`，输入任意合法邮箱。登录链接写入未跟踪的 `.cache/auth-outbox/`，复制最新 `.txt` 中的链接即可登录。该文件作为本地登录凭证保存。

Magic Link 默认 10 分钟有效、只能使用一次。Session 默认有效 24 小时，刷新页面和重启服务后仍有效，退出登录会立即撤销。管理员访问由环境变量中的邮箱列表控制。

## 生产环境

在服务器受保护的 `/etc/candidate-rag-assistant.env` 中设置环境变量：

```dotenv
APP_ENV=production
DEBUG=false
AUTH_PUBLIC_URL=https://your-domain-or-ip
AUTH_DELIVERY=smtp
INSPECTOR_ADMIN_EMAILS=["admin@example.com"]
AUTH_EMAIL_COOLDOWN_SECONDS=60
AUTH_IP_WINDOW_SECONDS=900
AUTH_IP_MAX_REQUESTS=20
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USERNAME=your@qq.com
SMTP_PASSWORD=your_smtp_authorization_code
SMTP_FROM=your@qq.com
```

QQ 邮箱须先开启 SMTP，`SMTP_PASSWORD` 填 QQ 提供的 SMTP 授权码；`SMTP_FROM` 与 `SMTP_USERNAME` 使用同一个 QQ 邮箱。生产环境必须使用可信 HTTPS 地址与 SMTP；`AUTH_DELIVERY=local`、Debug 模式和不安全 Cookie 都会被配置校验拒绝。Cookie 使用 HttpOnly、Secure、SameSite=Strict。Magic Link 指向 `/login#token=...`，片段不会进入 Web 服务器访问日志；同源验证完成后才设置 Session Cookie。

## QQ SMTP 发送测试

在服务器上用 `sudoedit /etc/candidate-rag-assistant.env` 填写实际的
`SMTP_USERNAME`、`SMTP_PASSWORD`（QQ SMTP 授权码）和 `SMTP_FROM`，并设置
`AUTH_DELIVERY=smtp`、`SMTP_HOST=smtp.qq.com`、`SMTP_PORT=465`、
`SMTP_SECURITY=ssl`。环境文件保持 root 所有、权限 600。

从项目目录执行以下一次性测试；默认给 `SMTP_USERNAME` 自己发送一封真正可单次使用的
Magic Link 邮件，也可以在命令末尾加 `--to recipient@example.com`：

```bash
sudo systemd-run --wait --pipe \
  --property=User=ubuntu \
  --property=WorkingDirectory=/home/ubuntu/candidate-rag-assistant \
  --property=EnvironmentFile=/etc/candidate-rag-assistant.env \
  /home/ubuntu/candidate-rag-assistant/.venv/bin/python \
  /home/ubuntu/candidate-rag-assistant/scripts/test_smtp.py
```

脚本分别验证 SSL 证书和授权码认证，再复用现有 AuthStore 与邮件发送代码注册并投递
Magic Link。输出阶段结果或错误类型。
完成投递后，在收件箱或垃圾邮件目录确认邮件，并点击链接验证登录。
同一邮箱在默认 60 秒冷却期内不会再次发送。当前若
`AUTH_PUBLIC_URL=http://127.0.0.1:8000` 时，链接在本机浏览器使用。外部访问配置 HTTPS 公共地址，并将
`APP_ENV` 切到 `production`。此时再重启 systemd 服务并实际点击链接验证登录。

邮箱冷却默认 60 秒；同一来源 IP 默认每 15 分钟最多 20 次合法格式的申请。被限频、邮件发送失败和正常申请都返回相同提示；只有邮箱格式错误返回 422。限频记录存于 Auth SQLite，服务重启后仍有效。反向代理必须覆盖客户端提供的 `X-Forwarded-For`，并把真实客户端 IP 传给仅监听 `127.0.0.1:8000` 的 Uvicorn；应用对可信代理传递的客户端 IP 独立限流。

`data/auth/auth.sqlite3` 保存登录链接与 Session 的哈希、到期时间，限频用的邮箱/IP 哈希，以及登录/退出事件（邮箱、随机会话 ID、UTC Unix 时间）。正式部署时将 Auth/Feedback SQLite 和模型缓存放在持久目录，限制文件权限，作为私有运行数据维护。

`SESSION_TTL_SECONDS` 是 RAG 对话闲置时间，与 `AUTH_SESSION_TTL_SECONDS` 不同。本机 `scripts/evaluate_api.py` 会读取本地收件目录登录；SMTP 部署时可在当前终端设置 `RAG_EVAL_SESSION_COOKIE`。
