# Candidate RAG Assistant · Public Demo

面向候选人资料问答的 RAG 项目。系统从 Markdown 检索证据，判断问题可回答性，生成答案后检查引用和事实支持度，再向用户发布正文。邮箱登录、会话隔离、管理员诊断和反馈回归组成完整的问答与改进流程。

演示知识库采用虚构候选人和项目样例。

## 项目能力与代码入口

```text
Markdown → 分节/滑窗分块 → BM25 + Embedding/FAISS
         → RRF → 可选 Cross-Encoder → 有界上下文补充
         → Evidence Gate → 生成 → 引用检查 → 事实核验
         → 已核验正文 / 固定拒答
历史 + 当前问题 → Query Rewrite → 检索
```

- `backend/app/rag/pipeline.py`：显式组织检索、证据选择、生成和核验，便于逐阶段阅读与调试。
- `rag/bm25.py`、`dense.py`、`hybrid.py`：融合关键词匹配和向量语义检索，兼顾技术词与改写问题。
- `rag/answerability.py`、`llm/client.py`：证据门控、结构校验、引用检查和生成后事实核验。
- `api/chat.py`、`request_control.py`：请求准入、协作式取消和核验后发布。
- `auth.py`：一次性邮箱登录链接、会话隔离、可信代理 IP 解析和管理员权限。
- `inspector.py`、`feedback_loop.py`：检索链路诊断、用户反馈、人工归因和回归案例管理。

普通聊天页采用简洁正文布局，隐藏引用 ID。`/chat` 与 `/chat/stream` 的最终响应返回 `sources`，管理员 Inspector 展示原文证据与各阶段结果。

## 本地运行

使用 Python 3.12.13。安装依赖，复制 `.env.example`，填写自己的 `LLM_API_KEY` 和 `LLM_MODEL`，然后启动服务。首次运行会下载 Embedding 和重排模型。

Windows PowerShell：

```powershell
git clone https://github.com/Kane-952/candidate-rag-assistant-public.git
cd candidate-rag-assistant-public
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
Copy-Item .env.example .env
# 用本地编辑器填写 LLM_API_KEY 和 LLM_MODEL
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

打开 `http://127.0.0.1:8000/`，填写示例邮箱 `reviewer@example.com`。默认 `AUTH_DELIVERY=local` 将登录链接写入 `.cache/auth-outbox/` 的新 `.txt` 文件；复制链接，在本地浏览器打开即可登录。设置 `INSPECTOR_ADMIN_EMAILS=["reviewer@example.com"]` 并重启后，该邮箱可以访问管理员页面。

`start_local.py` 检查知识库和索引，在资料变化时自动重建索引，再启动回环地址服务。手动启动方式：

```bash
python scripts/build_index.py
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

重建索引时先停止 API，完成后重启。配置、认证数据库、索引和日志保存在本地运行目录，已由 `.gitignore` 排除。

## 配置与执行机制

`.env.example` 提供默认配置。模型服务使用 `/chat/completions`，支持 system/user messages 和 JSON 文本回答；`LLM_BASE_URL` 填 API 根地址。DeepSeek thinking 扩展通过 `LLM_THINKING_MODE` 显式配置。

默认 Embedding 为 `BAAI/bge-small-zh-v1.5`，重排模型为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，在 CPU 上运行。FastAPI 提供服务，原生 HTML/CSS/JavaScript 实现前端。

首轮问答依次调用模型完成证据判断、回答生成和事实核验；多轮追问增加问题改写。问题与相关证据发送到配置的模型服务，产生相应 API 用量。

`MAX_PENDING_CHATS=4` 定义执行中与排队中的聊天请求总上限。满额返回 HTTP 429 和 `Retry-After`，全局锁串行执行推理。流式连接断开后取消排队和后续阶段；已经发出的 HTTP 请求在响应或超时后退出。`LLM_TIMEOUT_SECONDS` 设置单次 HTTP 请求的超时。

流式接口立即发送 `start`。内部生成采用 SSE，完成引用与事实核验后，发送正文 `delta` 和最终 `done`；拒答发送 `done`，故障发送 `error`。客户端在故障时清空本条正文，成功回答写入会话与反馈库。

## 测试与评测

离线回归结果：**75 项通过，2 项依赖弃用警告**，环境为 Python 3.12.13。测试使用模型替身、MockTransport 和临时数据库，覆盖分块、检索融合、证据门控、引用、鉴权、反馈、健康检查、核验后发布、代理限流、请求准入与取消。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
```

运行服务后执行 `python scripts/evaluate_api.py`，检查项目问答、追问、拒答和引用一致性。默认使用 `reviewer@example.com` 完成本地登录；`RAG_EVAL_EMAIL` 可指定其他邮箱。SMTP 模式使用当前终端的 `RAG_EVAL_SESSION_COOKIE` 登录。评测调用配置的模型 API，结果保存为 `data/processed/live_evaluation.json`。

详细结果见 [验证记录](evaluation/VALIDATION.md)，用例和流程见 [评测说明](evaluation/README.md)。

## 部署

生产环境使用单 worker 和 HTTPS，设置 `APP_ENV=production`、`AUTH_DELIVERY=smtp`、管理员邮箱、SMTP 与 LLM 配置。Auth/Feedback SQLite 放在受保护的持久目录。

同机 Nginx/Caddy 代理：后端绑定 `127.0.0.1`，启动参数使用 `--no-proxy-headers`。代理覆盖 `X-Forwarded-For` 和 `X-Real-IP` 为客户端地址，应用对回环连接解析可信转发信息。登录申请限流与登录日志复用相同解析。

`/health` 检查数据库、索引和预热模型，返回 `{"status":"ok"}` 或 `{"status":"unavailable"}`。

Render 构建命令：

```bash
pip install -r backend/requirements.txt && python scripts/build_index.py
```

启动命令：

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --workers 1
```

配置持久存储后，将健康检查路径设为 `/health`。

## 局限性与后续改进

当前版本读取 Markdown，使用单进程串行推理和内存会话。后续可扩展 PDF/Word 解析、依赖锁定、容器化部署、持久会话和并发调度。

已完成离线工程回归；真实模型问答质量、SMTP 投递和部署容量将通过端到端评测验证。模型负责证据判断与事实核验，质量评测需要标注问题集和人工复核。

## 手动更新与许可

展示仓库使用独立历史。更新时导出选定源 commit，替换资料与身份资源，检查差异，完成敏感信息扫描和测试后提交。操作步骤见 [手动更新说明](docs/PUBLIC_SYNC.md)。

项目当前未设置许可证，依赖与资源按各自许可使用。

## English overview

Candidate RAG Assistant answers questions about candidate profiles using Markdown ingestion, BM25 and FAISS dense retrieval, RRF fusion, optional reranking, evidence gating, citation checks and post-generation verification. The demo uses synthetic candidate data.

Email sessions isolate conversations. Administrator tools expose retrieval traces and feedback analysis. The chat stream publishes verified text, bounds pending requests and supports cooperative cancellation. API responses retain source evidence while the chat UI uses a concise text layout.

The offline suite passes 75 tests covering the RAG workflow, authentication, feedback and request control. Local setup uses Python 3.12.13 and user-provided model credentials.
