# maxcompute-semantic (`mcs`)

[![PyPI](https://img.shields.io/pypi/v/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![Python](https://img.shields.io/pypi/pyversions/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![License](https://img.shields.io/github/license/aliyun/maxcompute-semantic)](LICENSE)
[![CI](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml)

[![English](https://img.shields.io/badge/lang-English-blue)](README.md)
[![中文](https://img.shields.io/badge/lang-中文-red)](README.zh-cn.md)

**让你的 AI Agent 真正理解你的 MaxCompute 数据。**

`mcs` 在本地构建语义包——表描述、字段含义、JOIN 关系、已验证的 SQL 模式和业务指标——让
AI Agent 第一次就能写出正确的 MaxCompute SQL，而不是反复试错。

[文档](https://aliyun.github.io/maxcompute-semantic/zh-cn/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [更新日志](CHANGELOG.md)

## 为什么用 mcs？

AI Agent 能查询 MaxCompute，但它不了解**你的**数据。它会猜表名、漏 JOIN 条件、写出
失败或返回错误结果的 SQL。

`mcs` 填补这个缺口：

- **语义包** — `mcs build` 扫描项目 schema，生成结构化知识库（SQLite + markdown），
  Agent 写 SQL 前先读它。
- **记忆** — 验证过的查询、失败模式、领域知识随使用积累。Agent 用得越多越准。
- **SQL 护栏** — 费用估算、写保护、方言审查、tier 感知的 schema 解析，SQL 到达
  MaxCompute 之前全部检查完。
- **Agent 无关** — 支持 Claude Code、Cursor、Codex、Gemini CLI、Qwen Code、
  OpenCode 等 50+ 平台。一条 `mcs skill install --all` 让所有 Agent 自动加载。

## 快速开始

```bash
# 1. 安装
uv tool install maxcompute-semantic

# 2. 创建 profile（交互式向导）
mcs profile create
mcs link bind <profile-name>

# 3. 构建语义包
mcs build

# 完成 — Agent 现在可以通过 skill 使用 mcs 了。
```

## 安装

### 手动安装

```bash
uv tool install maxcompute-semantic    # 推荐
# 或者: pip install maxcompute-semantic
```

如果 `~/.local/bin` 不在 PATH 里：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

### 让 Agent 安装

把下面这段话发给你的 Agent，它会自动完成所有步骤：

```
帮我安装 mcs，先读完这个指南再逐步执行：
curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md
```

### 注册 Skill

```bash
mcs skill install --all -g     # 所有支持的 Agent，全局安装
mcs skill install --detect -g  # 仅安装到检测到的 Agent
```

支持的 Agent：`claude-code`、`cursor`、`codex`、`gemini-cli`、`qwen-code`、
`opencode` 等 [50+ 平台](https://aliyun.github.io/maxcompute-semantic/zh-cn/docs.html)。

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

## 配置

Profile 存储认证信息、计算项目、数据源和费用阈值：

```bash
mcs profile create                    # 交互式向导
mcs profile create --from-file @p.yaml  # 脚本化
mcs link bind <name>                  # 绑定当前目录到 profile
```

Profile 解析顺序：`--profile` 参数 → `MCS_PROFILE` 环境变量 → 目录绑定 → ODPS 环境变量。

CI 或一次性使用时可跳过 profile，直接设环境变量：

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
export MAXCOMPUTE_ENDPOINT=https://service.<region>.maxcompute.aliyun.com/api
export MAXCOMPUTE_PROJECT=<project>
```

## 参与贡献

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run ruff check src/ tests/
uv run mypy src/
```

## 许可证

Apache License 2.0 — 见 [LICENSE](LICENSE)。第三方声明见 [NOTICE](NOTICE)。
