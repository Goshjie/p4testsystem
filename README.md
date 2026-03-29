# P4TestSystem

意图驱动的 P4 测试系统 — 将自然语言测试意图转换为形式化规范，再生成可执行测试用例。

## 系统架构

```
自然语言意图 ──→ P4LTL 规范 ──→ 测试用例 ──→ 自动测试（后续）
                (P4LTL_LLM)    (SageFuzz)    (LLM Agent)
```

## 依赖

- Python 3.9+
- [P4LTL_LLM](/home/gosh/P4LTL/P4LTL_LLM) — 自然语言意图到 P4LTL 规范转换
- [SageFuzz](/home/gosh/SageFuzz) — 测试用例自动生成

## 快速使用

```bash
# 交互式选择测试用例
python cli.py

# 指定 case 直接运行
python cli.py --case-id "sagefuzz:firewall:block-new-external" --no-confirm

# 仅生成规范（不生成测试用例）
python cli.py --spec-only
```

## 目录结构

```
P4TestSystem/
├── cli.py                        # CLI 入口
├── requirements.txt
├── system/
│   ├── models.py                 # 统一数据模型 (SessionTask)
│   ├── orchestrator.py           # 会话编排器
│   ├── intent_adapter.py         # 意图格式适配
│   ├── programs/
│   │   └── program_registry.py   # P4 程序/用例注册
│   ├── agent/                    # LLM Test Agent (后续实现)
│   ├── api/                      # Web API (后续实现)
│   └── frontend/                 # Web 前端 (后续实现)
└── docs/
    └── design.md                 # 设计文档
```
