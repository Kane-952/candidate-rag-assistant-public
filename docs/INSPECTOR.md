# RAG Inspector

在未跟踪的 `.env` 或服务器环境文件中设置 `INSPECTOR_ADMIN_EMAILS=["admin@example.com"]`。管理员必须先通过 Magic Link 验证，再用该 Session 打开 `/inspector`。未配置管理员时，调试记录仍为反馈闭环采集，但页面和 API 均不开放；普通聊天页不显示调试入口。未登录访问返回 401，已登录的非管理员访问页面、静态资源或管理 API 均返回 403。

“最近问答”默认显示最近 50 条，可按完整登录邮箱筛选。普通 Trace 只保留最近 200 条；已收到反馈或加入回归集的 Trace 会继续保留。每条记录保存后端已验证的 `user_email`、聊天 `conversation_id`、原始问题和 UTC `timestamp`。同一邮箱的不同对话按 `conversation_id` 分开；它不是登录 Session Token。记录还包含多轮历史、Query Rewrite、Retrieval Top-K、Rerank、最终 Context、生成和核验、引用及阶段耗时。

“Feedback · 点踩分析”显示所有当前有效点踩，可按邮箱、用户体验原因、`ANSWER / PARTIAL / REFUSE` 筛选。展开详情可查看完整 RAG 链路、`user_feedback_reason`、`user_comment`、独立保存的 `admin_root_cause`、处理状态和回归案例。系统不会将用户的体验选项自动判成技术故障；管理员应先查看正确依据是否检索到、是否进入最终 Context，再判断生成、拒答、引用、回答风格或资料不足问题。

`ANSWER` 表示完成回答，`REFUSE` 表示现有证据门或最终校验拒答。`PARTIAL` 表示最终回答文本含未确认部分的标记，不是独立质量判断。核验/生成故障不向用户发送草稿，记录为 `ERROR`；管理员诊断可能包含内部生成过程，但不代表已发布答案。

数据库位于已被 `.gitignore` 排除的 `data/feedback/feedback.sqlite3`。调试记录不保存 API Key、Cookie、Magic Link、验证码、Session Token 或 System Prompt；输入中的常见凭据格式在保存前脱敏。只有管理员接口会返回其他人的邮箱和反馈。采集不改变检索、拒答、引用或生成策略。
