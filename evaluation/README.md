# DEMO 评测

`questions.jsonl` 检查虚构知识库的项目问答、混合检索理由和资料不足拒答。脚本额外插入同会话“为什么这么做？”追问，验证问题改写和项目来源。

配置模型并启动本地服务后，运行 `python scripts/evaluate_api.py`。脚本完成邮箱登录，检查 `/health` 的 `status`，逐项核对返回来源与索引的 ID、路径、标题和原文。结果保存到 `data/processed/live_evaluation.json`。

各脚本分工：

- `evaluate_api.py`：项目问答、追问、拒答和引用一致性。
- `evaluate_profile_fix.py`：同一评测的兼容入口。
- `benchmark_latency.py`：核验后首段正文与完整回答耗时。
- `run_feedback_regressions.py`：执行管理员整理的反馈回归案例。

这些脚本调用配置的模型 API。更新知识库时同步调整题目、可回答标注和期望来源；结果说明见 [验证记录](VALIDATION.md)。
