# Candidate RAG Assistant · Public Demo

一个面向候选人资料问答的 RAG 工程展示：从 Markdown 检索证据，用模型判断可回答性，生成答案后检查引用和事实支持度，再向用户展示结果。知识库、人物身份和项目经历全部为专门编写的虚构 DEMO；不是作者简历，也不代表真实求职者。

## 工程内容与阅读入口

```text
Markdown → 分节/滑窗分块 → BM25 + Embedding/FAISS
         → RRF → 可选 Cross-Encoder → 有界上下文补充
         → Evidence Gate → 生成 → 引用检查 → 事实核验
         → 已核验正文 / 固定拒答
历史 + 当前问题 → Query Rewrite → 检索
```

- `backend/app/rag/pipeline.py`：显式检索和回答链路，没有依赖 LangChain/LangGraph。
- `rag/bm25.py`、`dense.py`、`hybrid.py`：关键词、向量检索和排序融合。
- `rag/answerability.py`、`llm/client.py`：证据门控、结构校验、生成后核验和安全错误消息。
- `api/chat.py`、`request_control.py`：有界请求准入、协作式取消、核验后发布。
- `auth.py`：单次邮箱登录链接、会话隔离、可信代理 IP 解析和管理员权限。
- `inspector.py`、`feedback_loop.py`：管理员诊断、用户反馈和人工整理的回归案例。

普通聊天页保留简洁正文，隐藏引用 ID，不展示来源面板。`/chat` 与 `/chat/stream` 的最终响应仍返回 `sources`；管理员 Inspector 可以查看证据。引用存在不保证语义正确，模型自评也不是校准概率。

## 本地运行

测试使用 Python 3.12.13；`.python-version` 指定 3.12.13。以下步骤需要网络下载依赖和模型，并由使用者自行填写 LLM 配置。没有随仓库提供 API key、SMTP 授权码、预建索引或个人运行数据库。

Windows PowerShell：

```powershell
git clone https://github.com/Kane-952/candidate-rag-assistant-public.git
cd candidate-rag-assistant-public
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
# 用本地编辑器填写 .env 中的 LLM_API_KEY 和 LLM_MODEL
.\.venv\Scripts\python.exe scripts/start_local.py
```

macOS / Linux：

```bash
git clone https://github.com/Kane-952/candidate-rag-assistant-public.git
cd candidate-rag-assistant-public
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cp .env.example .env
# 用本地编辑器填写 LLM_API_KEY 和 LLM_MODEL
.venv/bin/python scripts/start_local.py
```

打开 `http://127.0.0.1:8000/`，填写假邮箱 `reviewer@example.com`。默认 `AUTH_DELIVERY=local` 不发邮件，登录链接写入 `.cache/auth-outbox/` 的新 `.txt`，复制链接在本地浏览器打开。管理员可在自己的 `.env` 设置 `INSPECTOR_ADMIN_EMAILS=["reviewer@example.com"]` 后重启。登录链接与数据库不要提交或分享。

`start_local.py` 在知识库或索引不一致时重建索引，再启动仅绑定回环地址的服务。也可手动运行 `python scripts/build_index.py`，随后 `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-access-log`。重建索引前停止 API，完成后重启。

## 配置与资源

`.env.example` 给出默认配置；LLM 服务需提供 `/chat/completions`，支持 system/user messages 和 JSON 文本回答。`LLM_BASE_URL` 填 API 根地址，不填完整 `/chat/completions`。DeepSeek 的 thinking 扩展仅在显式设置时发送。

默认 Embedding 为 `BAAI/bge-small-zh-v1.5`，重排为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，使用 CPU，无需 GPU 或 Node.js。首次运行下载模型；没有验证所有机器的安装兼容性或启动耗时。依赖目前使用范围版本，没有锁文件。

一次正常首轮包含证据判断、生成、核验等模型请求；有历史时还可能改写问题，会产生服务商用量。只有必要问题与证据发送至使用者配置的服务商。不要填入不愿外发的数据。

`MAX_PENDING_CHATS=4` 限制执行中加排队的聊天请求总数，满额返回 HTTP 429 和 `Retry-After`。推理仍由全局锁串行执行，要求单 worker。断开流式连接会取消等待和后续阶段；已发出的 HTTP 请求不能保证立即撤回，最多等待请求响应或超时后退出。`LLM_TIMEOUT_SECONDS` 是 HTTP 超时配置，不是整个问答的总时限。

## 核验后输出

流式接口先发送 `start`，内部模型生成可以采用 SSE，但其草稿不传给浏览器。引用和事实核验通过后发送已核验正文的 `delta` 与最终 `done`；拒答只发送 `done`；故障发送 `error`，不展示草稿、不写成功历史。等待首段正文会比直接展示未核验 token 更久。

客户端在错误时清除本条正文。模型核验仍可能判断错误，该机制不保证永不幻觉或能防御全部提示注入。

## 测试与评测

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

单元/回归测试使用模型替身、MockTransport 和临时数据库，不消耗模型 API 用量。覆盖分块、融合、证据与拒答、引用、鉴权、反馈、readiness，以及修复后的核验后发布、代理限流、准入与取消、评测登录和健康接口契约。

服务启动且本地登录配置完成后，可运行 `python scripts/evaluate_api.py`。默认使用 `reviewer@example.com` 完成本地登录；修改邮箱可设置环境变量 `RAG_EVAL_EMAIL`。SMTP 模式需先登录并在当前终端设置 `RAG_EVAL_SESSION_COOKIE`；不要把 Cookie 放到文件或命令历史。该评测会调用真实模型，产生费用，结果写入被忽略的 `data/processed/live_evaluation.json`。

本展示版未执行真实模型端到端验收、在线部署或负载测试，不沿用原项目历史 DEMO/真实资料的测试成绩和延迟数字。当前验证范围见 [evaluation/VALIDATION.md](evaluation/VALIDATION.md)。

## 部署说明与限制

此仓库提供代码和本地运行方式，没有独立在线演示站点。若自行部署，使用单 worker 和 HTTPS；设置 `APP_ENV=production`、`AUTH_DELIVERY=smtp`、管理员邮箱、SMTP 和 LLM 配置，将 Auth/Feedback SQLite 放在受保护的持久目录。生产配置校验拒绝本地投递模式、不安全公共地址及 debug。

同机 Nginx/Caddy 代理：后端只绑定 `127.0.0.1`，启动使用 `--no-proxy-headers`，代理覆盖 `X-Forwarded-For` 和 `X-Real-IP` 为可信客户端地址。应用仅信任回环连接提供的转发信息，登录申请限流与登录日志复用相同解析。不要直接暴露该后端端口。

`/health` 无需登录，返回 `{"status":"ok"}` 或 `{"status":"unavailable"}`；检查数据库、索引和预热模型，不调用远端 LLM。它不证明远端 API 可用。Render 如自行使用：构建为 `pip install -r backend/requirements.txt && python scripts/build_index.py`，启动为 `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --workers 1`，配置持久存储后再启用健康检查。目标平台安装和代理行为需另行验收。

尚未提供 PDF/Word 解析、Docker、正式检索质量基准、分布式并发、持久聊天历史恢复或 Agent 工具调用。反馈和认证数据库含运行中的邮箱、IP 和问答，始终作为私有运行数据。

## 手动更新与许可

这是独立展示仓库，使用全新历史。后续更新需导出选定源 commit，重新替换真实资料、身份资源和配置，检查差异、扫描候选树并运行测试后提交，不能 mirror 或合并原项目历史。详见 [手动更新说明](docs/PUBLIC_SYNC.md)。

暂未新增项目许可证；仓库公开用于阅读与评估。依赖与资源的使用仍需遵守各自许可。

## English overview

Candidate RAG Assistant is a source-code portfolio demo with entirely synthetic candidate data. It implements Markdown ingestion, BM25 and FAISS dense retrieval, RRF fusion, optional reranking, evidence gating, citation checks and post-generation verification. Email sessions isolate conversations; administrator tools support diagnostics and feedback review.

Only verified final text is published to the chat stream. Admission is bounded and disconnect cancellation is cooperative. The simple chat UI hides citations; API responses retain source evidence. Tests use model doubles and temporary storage. No live-model benchmark, independent hosted demo or production capacity claim is provided. Bring your own credentials; never commit runtime data.
