# P4TestSystem

意图驱动的 P4 测试系统 — 将自然语言测试意图转换为形式化规范，再生成可执行测试用例，并通过 LLM Agent 在远程环境自动执行测试。

## 系统架构

```
自然语言意图 ──→ P4LTL 规范 ──→ 测试用例 ──→ 自动测试 ──→ 结果判别
                (P4LTL_LLM)    (SageFuzz)    (LLM Agent)   (LLM Judge)
```

## 依赖

- Python 3.12+
- [P4LTL_LLM](/home/gosh/P4LTL/P4LTL_LLM) — 自然语言意图到 P4LTL 规范转换
- [SageFuzz](/home/gosh/SageFuzz) — 测试用例自动生成

## 安装

```bash
pip3.12 install -r requirements.txt
```

## 使用方式

### CLI（命令行）

```bash
# 交互式选择测试用例
python3.12 cli.py

# 指定 case 直接运行
python3.12 cli.py --case-id "sagefuzz:firewall:block-new-external" --no-confirm

# 仅生成规范
python3.12 cli.py --spec-only

# 完整流程含自动测试
python3.12 cli.py --auto-test --no-confirm
```

### Web 界面

```bash
# 启动 Web 服务
python3.12 -m uvicorn system.api.app:app --host 0.0.0.0 --port 8000

# 访问浏览器
# http://localhost:8000
```

Web 界面提供四个区域：
1. **输入区** — 选择 P4 程序、编辑测试意图
2. **规范展示区** — 原始意图与 P4LTL 规范对照、校验状态
3. **测试用例区** — 数据包序列、控制平面规则、Oracle 预测
4. **测试结果区** — Agent 执行进度、逐包判定结论

## 目录结构

```
P4TestSystem/
├── cli.py                           # CLI 入口
├── requirements.txt
├── system/
│   ├── models.py                    # 统一数据模型 (SessionTask)
│   ├── orchestrator.py              # 会话编排器
│   ├── intent_adapter.py            # 意图格式适配
│   ├── programs/
│   │   └── program_registry.py      # P4 程序/用例注册
│   ├── agent/                       # LLM Test Agent
│   │   ├── tools.py                 # SSH 远程工具层
│   │   ├── test_agent.py            # ReAct Agent 核心
│   │   ├── judge.py                 # 结果判别模块
│   │   └── prompts/                 # Agent/Judge 系统提示词
│   ├── api/
│   │   └── app.py                   # FastAPI Web 后端
│   └── frontend/
│       └── index.html               # Web 前端（单页应用）
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/programs` | 获取可选的 P4 程序列表 |
| POST | `/api/spec/generate` | 生成 P4LTL 规范 |
| POST | `/api/testcase/generate` | 生成测试用例 |
| POST | `/api/test/run` | 触发自动测试 |
| GET | `/api/test/stream/{task_id}` | SSE 实时进度 |
| GET | `/api/task/{task_id}` | 查询任务状态 |
