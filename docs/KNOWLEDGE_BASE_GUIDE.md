# 虚构知识库的编辑

当前仓库包含 profile/demo_profile.md 与 projects/demo_rag.md 两份明确标记的虚构资料。公开展示版始终保留虚构身份，不填写真实简历、联系方式、学校、单位或用户问答。

仅读取 UTF-8 Markdown。按主题与标题分节，项目文档说明虚构背景、方法和设计理由，不编造可误认为作者成果的数字。未记录的信息通过拒答题检验，不能把未记录描述为从未发生。

更新资料后停止 API，运行 python scripts/build_index.py，然后重启。索引含原文，不进入 Git。同步更新 evaluation/questions.jsonl 的可回答标注与 expected_source，再用自己的模型独立验收；真实 API 脚本会产生用量。

若将本代码用于真实个人资料，应在独立私有副本中操作，重新评估资料外发与访问范围，并确保不将真实数据合入公开展示仓库。
