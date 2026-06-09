# maxcompute-semantic (`mcs`)

[![PyPI](https://img.shields.io/pypi/v/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![Python](https://img.shields.io/pypi/pyversions/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![License](https://img.shields.io/github/license/aliyun/maxcompute-semantic)](LICENSE)
[![CI](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml)
[![English](https://img.shields.io/badge/lang-English-blue)](README.md)
[![中文](https://img.shields.io/badge/lang-中文-red)](README.zh-cn.md)

**语义包 + 记忆库，AI Agent 越用越懂你的 MaxCompute。**

`mcs` 在本地构建语义包——表描述、字段含义、JOIN 关系、已验证的 SQL 模式和业务指标——让
AI Agent 第一次就能写出正确的 MaxCompute SQL，而不是反复试错。

[文档](https://aliyun.github.io/maxcompute-semantic/zh-cn/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [更新日志](CHANGELOG.md)

## 三件事

**你配 `profile` → `mcs build` 出语义包 → agent 直读它写 SQL**

- **`[A] profile`** —— 你配的一份"我是谁 + 看哪些表"（AK / 免密 + 一组 source）。一份 profile = 一个业务场景。
- **`[B] 语义包`** —— `mcs build` 出来的本地知识库（表 / 列 / JOIN / UDF），agent 写 SQL 前先读它，省去每次现翻 MaxCompute meta。
- **`[C] agent`** —— 通过 SKILL.md 接入；过 `mcs sql cost` 估价闸门后跑 `mcs sql execute` 执行。

业务场景 = `[A]` profile + `[B]` 语义包 + 累积的 *annotate* / *memory*（沉淀越久越准）。

## 为什么用 mcs？

AI Agent 能查询 MaxCompute，但它不了解**你的**数据。它会猜表名、漏 JOIN 条件、写出
失败或返回错误结果的 SQL。

`mcs` 填补这个缺口：

- **语义包** — `mcs build` 扫描项目 schema，生成结构化知识库（SQLite + markdown），Agent 写 SQL 前先读它。
- **记忆** — 验证过的查询、失败模式、领域知识随使用积累。Agent 用得越多越准。
- **SQL 护栏** — 费用估算、写保护、方言审查、tier 感知的 schema 解析，SQL 到达 MaxCompute 之前全部检查完。
- **Agent 无关** — 支持 Claude Code、Cursor、Codex、Gemini CLI、Qwen Code、OpenCode 等 50+ 平台。一条 `mcs skill install --all` 让所有 Agent 自动加载。

## 快速上手

### 1. 安装

在任何已联网的 AI Agent 里说一句：

> 帮我安装 mcs，先读完这个指南再逐步执行：curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md

Agent 会把 CLI 和 skill 一次性装好。不想让 agent 装的话，走[手动安装](#手动安装)。

### 2. 让 agent 帮你建语义层

skill 装好之后，**先和 agent 聊清楚你的业务场景**，让它把 profile + 语义包建出来：

> *"我在做数仓 A 的月度分析，主要看 `your_project` 里 dwd / dws 这两层的订单和用户表，帮我建一下语义层"*

agent 会按这个流程搭起来：

1. **profile create** — 引导你接入 MaxCompute 身份，向导自动探活。
2. **link bind** — 把当前目录绑定到 profile，后续命令自动认它。
3. **mcs build** — 扫一遍所有表，落到本地语义包。

### 3. 用自然语言提问

> *"上个月订单 GMV 同比怎么样"*

agent 走 `mcs show`（读语义包）→ `mcs sql cost`（估价闸门）→ `mcs sql execute`（执行）。跑通的 SQL 再让它 `mcs memory verify` 存一下，下次相似问题直接召回。

## 核心功能

| 功能 | 命令 | 说明 |
|------|------|------|
| **构建** | `mcs build` | 扫描 schema → 生成语义包 |
| **查询** | `mcs sql execute '...'` | tier 感知的 SQL 执行 |
| **费用估算** | `mcs sql cost '...'` | 执行前预估费用 |
| **SQL 审查** | `mcs sql review '...'` | 方言 / schema 问题检查 |
| **记忆** | `mcs memory verify ...` | 记录已验证的查询供未来召回 |
| **召回** | `mcs memory recall '<q>'` | BM25 搜索已验证的 SQL + 笔记 |
| **指标** | `mcs metric add ...` | 定义可复用的业务指标 |
| **建议** | `mcs package propose` | 从构建结果生成语义标注建议 |
| **诊断** | `mcs doctor` | 诊断 profile / 认证 / skill 状态 |

运行 `mcs <command> --help` 查看完整参数。

## 手动安装

```bash
uv tool install maxcompute-semantic    # 推荐
# 或者: pip install maxcompute-semantic
```

如果 `~/.local/bin` 不在 PATH 里：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

装完后把 skill 装进 Agent：

```bash
mcs skill install --all -g     # 所有支持的 Agent，全局安装
mcs skill install --detect -g  # 仅安装到检测到的 Agent
```

### 升级 / 卸载

```bash
mcs update                                    # 检查最新版并升级
uv tool uninstall maxcompute-semantic         # 卸载 CLI
mcs skill uninstall --all                     # 移除所有 Agent 的 skill 软链
```

## 配置

Profile 存储认证信息、计算项目、数据源和费用阈值：

```bash
mcs profile create                    # 交互式向导
mcs profile create --from-file @p.yaml  # 脚本化
mcs link bind <name>                  # 绑定当前目录到 profile
```

Profile 解析顺序：`--profile` 参数 → `MCS_PROFILE` 环境变量 → 目录绑定 → ODPS 环境变量。

## 参与贡献

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run ruff check src/ tests/
uv run mypy src/
```

## 许可证

Apache License 2.0 — 见 [LICENSE](LICENSE)。第三方声明见 [NOTICE](NOTICE)。
