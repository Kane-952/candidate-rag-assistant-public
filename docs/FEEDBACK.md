# 反馈闭环

聊天回答支持“赞 / 踩”，再次点击当前选项可取消。点踩选择使用体验：没有回答问题、回答不够清楚、太长、太简略、没有覆盖重点或其他；“其他”支持 300 字备注。管理员结合 Trace 分析技术原因。反馈权限由后端 Session 与所属会话校验。

## 数据与处理流程

`data/feedback/feedback.sqlite3` 保存独立 `answer_id`、`request_id`、当前 `like / dislike` 评价和变更历史。反馈快照包含邮箱、会话、问题、回答、引用、用户体验原因、备注，以及检索、重排、最终 Context 和各阶段耗时。管理员归因保存在 `admin_root_cause`。

Trace 在落盘后补齐反馈快照。取消评价移除当前案例，变更历史继续保留；旧版选项存于历史字段。

管理员在 `/inspector` 的 **Feedback · 点踩分析** 中筛选、查看和归因，再按“待处理 → 已分析 → 已修复 → 已加入回归测试”推进。已修复案例可以加入回归集。

## 回归与导出

添加回归案例时填写期望状态，可选填来源、回答关键词和行为说明。脚本检查状态、来源和关键词，管理员复核行为说明。

```powershell
.\.venv\Scripts\python.exe scripts/run_feedback_regressions.py
```

脚本使用真实 `/chat` 与配置的模型 API，结果保存为 `data/feedback/regression-results-*.json`。登录流程与 `scripts/evaluate_api.py` 相同。

导出当前反馈与 Trace：

```powershell
.\.venv\Scripts\python.exe scripts/export_feedback.py
```

回归案例和导出结果保存到被 Git 忽略的本地目录，作为管理员运行数据维护。
