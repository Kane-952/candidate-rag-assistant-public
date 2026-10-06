# 反馈闭环

登录后的聊天回答下方有“赞 / 踩”。点赞直接保存；点踩只询问使用体验：没有回答问题、回答不够清楚、太长、太简略、没有覆盖重点或其他。“其他”可选填不超过 300 字的备注。再次点击当前选项可取消。用户不需要判断事实准确性、检索或引用是否正确；这些由管理员查看 Trace 后归因。只有当前登录邮箱所属的会话能够提交该回答的反馈，邮箱由后端 Session 提供，不接受前端自报的身份。

每个回答在本地 `data/feedback/feedback.sqlite3` 中有独立 `answer_id` 和 `request_id`。当前评价在 `feedback_cases` 中使用 `like / dislike`；`feedback_events` 保存变更历史。反馈快照包含登录邮箱、聊天 `conversation_id`、问题、回答、引用、回答状态、`user_feedback_reason`、`user_comment`、时间，以及 Retrieval Top-K、Rerank、最终 Context 与各阶段耗时。`admin_root_cause` 单独保存管理员的技术归因。若用户在 Trace 落盘前立即点击，记录会在 Trace 写入时补齐。取消评价会移除当前反馈案例，但历史事件保留。旧版点踩原因保留在历史字段中，不冒充新版用户体验反馈。

管理员登录后打开 `/inspector` 的 **Feedback · 点踩分析** 区域，可以查看所有当前有效点踩，按邮箱、用户体验原因和回答状态筛选，再展开完整问答。管理员可人工标记 Retrieval、Generation / Prompt、Refusal、Citation、回答风格、知识库资料不足、其他，或暂留“待人工判断”，并设置“待处理 → 已分析 → 已修复 → 已加入回归测试”。只有已修复案例可以加入本地回归集。

加入回归集时填写期望回答状态，并可选填期望来源、回答关键词和人工期望说明。回归案例保存在未跟踪的本地数据库 `regression_cases`，不会把 HR 邮箱写进 Git。运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_feedback_regressions.py
```

该脚本会调用真实 `/chat` 接口，可能产生模型 API 费用；自动检查回答状态和选填的来源、关键词。期望行为说明需要人工复核，不能仅靠关键词判断事实是否正确。结果写入 `data/feedback/regression-results-*.json`。本地验证登录方式与 `scripts/evaluate_api.py` 相同。

如需导出当前有效反馈及完整 Trace，可运行：

```powershell
.\.venv\Scripts\python.exe scripts/export_feedback.py
```

导出保存在已被 `.gitignore` 排除的 `data/feedback/`。文件含问答和邮箱，只在受控环境中使用。
