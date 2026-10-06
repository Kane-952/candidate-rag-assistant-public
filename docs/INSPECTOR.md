# RAG Inspector

RAG Inspector 为管理员展示问答的检索路径、上下文、生成与核验结果、阶段耗时和用户反馈。

## 访问与记录

在 `.env` 或服务器环境中设置 `INSPECTOR_ADMIN_EMAILS=["admin@example.com"]`，通过 Magic Link 登录后打开 `/inspector`。管理员页面和 API 对未登录请求返回 401，对非管理员返回 403。

“最近问答”默认显示 50 条，支持完整邮箱筛选。普通 Trace 保留最近 200 条，已收到反馈或加入回归集的 Trace 持续保留。记录包括 `user_email`、聊天 `conversation_id`、原始问题、UTC `timestamp`、多轮历史、Query Rewrite、Retrieval Top-K、Rerank、最终 Context、生成、核验和阶段耗时。

## 反馈分析

“Feedback · 点踩分析”支持按邮箱、用户体验原因和回答状态筛选。详情包含完整链路、`user_feedback_reason`、`user_comment`、管理员 `admin_root_cause`、处理状态和回归案例。

管理员依次检查证据检索、Context 选择、生成和最终核验，确定检索、引用、拒答或回答风格等问题。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| `ANSWER` | 回答完成 |
| `REFUSE` | 证据门或最终核验拒答 |
| `PARTIAL` | 最终回答文本含未确认部分的标记 |
| `ERROR` | 请求处理失败 |

检索分数采用 RRF 排序分数，重排分数用于相关度排序。

## 存储

数据位于被 Git 忽略的 `data/feedback/feedback.sqlite3`。常见凭据格式在入库前脱敏，邮箱和反馈通过管理员接口读取。
