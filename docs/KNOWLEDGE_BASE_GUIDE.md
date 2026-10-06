# 编辑演示知识库

当前知识库包含 `profile/demo_profile.md` 和 `projects/demo_rag.md` 两份虚构资料，分别描述候选人背景和项目方法。

使用 UTF-8 Markdown，按主题与标题分节，写清项目背景、技术方法和选择理由。公开展示版使用虚构身份；真实资料放在独立私有副本中维护。

更新步骤：

1. 修改知识库与 `evaluation/questions.jsonl` 的可回答标注、`expected_source`。
2. 停止 API，运行 `python scripts/build_index.py`。
3. 重启服务，执行评测并核对引用。

索引保存在被 Git 忽略的本地目录。真实 API 评测使用自己的模型配置并产生相应用量。
