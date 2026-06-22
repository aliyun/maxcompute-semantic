# maxcompute-semantic (`mcs`)

[![PyPI](https://img.shields.io/pypi/v/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![Python](https://img.shields.io/pypi/pyversions/maxcompute-semantic)](https://pypi.org/project/maxcompute-semantic/)
[![License](https://img.shields.io/github/license/aliyun/maxcompute-semantic)](LICENSE)
[![CI](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml/badge.svg)](https://github.com/aliyun/maxcompute-semantic/actions/workflows/ci.yml)
[![English](https://img.shields.io/badge/lang-English-blue)](README.md)
[![中文](https://img.shields.io/badge/lang-中文-red)](README.zh-cn.md)

**让 AI Agent 带着上下文查询你的 MaxCompute。**

`mcs` 是一个 CLI，用来从 MaxCompute 项目构建本地语义包：表和字段说明、JOIN 关系、UDF、
业务指标、验证过的 SQL，以及历史查询沉淀下来的笔记。Agent 写 SQL 前先读这份语义包，
再通过 `mcs` 做 SQL 审查、费用预估和查询执行。

[文档](https://aliyun.github.io/maxcompute-semantic/zh-cn/) · [PyPI](https://pypi.org/project/maxcompute-semantic/) · [更新日志](CHANGELOG.md)

## 为什么用 mcs？

AI Agent 能调用 MaxCompute，但它不了解你的数仓。它会猜表名、漏 JOIN 条件，甚至写出能跑但答错问题的 SQL。

`mcs` 给 agent 一个本地的事实来源：

- **语义包** - `mcs build` 扫描表元数据，生成 agent 写 SQL 前可以读取的结构化知识库。
- **记忆** - 验证过的查询、失败模式和领域笔记会随使用积累。相似问题可以复用跑通过的 SQL。
- **SQL 护栏** - 费用估算、写保护、方言审查、tier 感知的 schema 解析，会在 SQL 到达 MaxCompute 前先跑一遍。
- **Agent 接入** - 内置 skill 支持 Claude Code、Cursor、Codex、Gemini CLI、Qwen Code、OpenCode 等平台。

## 工作方式

**你配 `profile` → `mcs build` 出语义包 → agent 直读它写 SQL**

- **`[A] profile`** - 身份、计算项目、费用阈值和表范围。一份 profile 通常对应一个业务场景。
- **`[B] 语义包`** - `mcs build` 生成的本地 SQLite + markdown 知识库。
- **`[C] agent`** - 通过生成的 skill 接入；读语义包，跑 `mcs sql cost`，再用
  `mcs sql execute` 或异步 SQL 生命周期执行查询。

业务场景 = profile + 语义包 + 指标 + 查询记忆。

## 快速上手

开始前，先准备好：MaxCompute project、region 或 endpoint、认证方式（AK / 免密 NCS / process）、
要覆盖的表或 schema，以及这些表的 SELECT 权限。

### 1. 安装

```bash
uv tool install maxcompute-semantic
mcs --version
```

然后把 skill 注册到本机检测到的 agent：

```bash
mcs skill install --detect -g
```

想让 agent 代装的话，在任何已联网的 AI Agent 里说一句：

> 帮我安装 mcs，先读完这个指南再逐步执行：curl -fsSL https://raw.githubusercontent.com/aliyun/maxcompute-semantic/main/scripts/install.md

安装指南会要求 agent 在执行远程 bootstrap 或最终安装命令前，先把具体命令展示给你。

### 2. 创建 profile

```bash
mcs profile create
mcs link bind <profile-name>
```

`profile create` 会打开交互式向导，收集 endpoint、认证方式、project、数据源和费用阈值。
`link bind` 会把当前目录绑定到这个 profile，后续命令自动识别。

### 3. 构建语义包

```bash
mcs build
mcs doctor
```

### 4. 通过 agent 提问

> *"上个月订单 GMV 同比怎么样"*

agent 会读 `mcs show`，用 `mcs sql review` 检查 SQL，用 `mcs sql cost` 预估费用，
确认安全后再跑 `mcs sql execute`。跑通的 SQL 再让它存一下，下次相似问题可以 BM25 召回：

```bash
mcs memory verify --question "上个月订单 GMV 同比怎么样" --sql "SELECT ..." --tables your_project.your_schema.orders
```

你也可以让 agent 代做第 2、3 步。装好 skill 后，直接描述业务场景：

> *"我在做数仓 A 的月度分析，主要看 `your_project` 里 dwd / dws 这两层的订单和用户表，帮我建一下语义层。"*

agent 会引导 profile 配置、绑定当前目录，并运行 `mcs build`。

## 安全与隐私

- `mcs` 把 profile 和语义包存放在本地。语义包是 SQLite + markdown，不是托管服务。
- 凭证保存在 profile 配置里。AK 可以用环境变量引用，不必写死明文。
- `mcs build` 只读取 profile 中声明的数据源；可选采样也只作用于这些表。
- `mcs sql cost`、`mcs sql review` 和写保护会在执行前拦截高费用或不安全 SQL。
- Agent 通过 `mcs` 这道闸门访问 MaxCompute，不需要在 profile 之外单独拿 MaxCompute 凭证。

## 核心功能

| 功能 | 命令 | 说明 |
|------|------|------|
| **构建** | `mcs build` | 扫描 schema → 生成语义包 |
| **查看** | `mcs show` / `mcs status` | 查看语义包数据和构建状态 |
| **元数据** | `mcs meta ...` | 发现 project、schema、table、column |
| **查询** | `mcs sql execute '...'` | tier 感知的 SQL 执行 |
| **异步 SQL** | `mcs sql submit` / `wait` / `result` | 提交并获取长查询结果 |
| **费用估算** | `mcs sql cost '...'` | 执行前预估费用 |
| **SQL 审查** | `mcs sql review '...'` | 方言 / schema 问题检查 |
| **记忆** | `mcs memory verify ...` | 记录已验证的查询供未来召回 |
| **召回** | `mcs memory recall '<q>'` | BM25 搜索已验证的 SQL + 笔记 |
| **指标** | `mcs metric add ...` | 定义可复用的业务指标 |
| **建议** | `mcs package propose --from-suggestions` | 把构建建议转成可审查的 package 变更 |
| **UDF** | `mcs udf ...` | 列出、查看、创建、测试和删除 UDF |
| **诊断** | `mcs doctor` | 诊断 profile / 认证 / skill 状态 |

运行 `mcs <command> --help` 查看完整参数。

## 手动安装

```bash
uv tool install maxcompute-semantic    # 推荐，Python >= 3.10
# 在 virtualenv / 自己管理的 Python 环境中: pip install maxcompute-semantic
```

如果 `~/.local/bin` 不在 PATH 里：

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

装完后把 skill 装进 Agent。二选一：

```bash
mcs skill install --detect -g  # 推荐：仅安装到本机检测到的 Agent
mcs skill install --all -g     # 安装到所有支持的 Agent 平台
```

### 升级 / 卸载

下面的卸载示例带 `-g`，因为上面的安装示例是全局安装；如果是本地 skill 安装，去掉 `-g`。

```bash
mcs update                                    # 检查最新版并升级
mcs skill uninstall --all -g                  # 移除全局 skill 软链
uv tool uninstall maxcompute-semantic         # 卸载 CLI
```

## 配置

Profile 存储认证信息、计算项目、数据源和费用阈值：

```bash
mcs profile create                    # 交互式向导
mcs profile create --from-file @p.yaml  # 脚本化
mcs link bind <name>                  # 绑定当前目录到 profile
```

Profile 解析顺序：`--profile` 参数 → `MCS_PROFILE` 环境变量 → 目录绑定 → `ALIBABA_CLOUD_*` / `MAXCOMPUTE_*` 环境变量。

## 参与贡献

```bash
uv sync --extra dev
uv run pytest tests/ -m 'not live'
uv run ruff check src/ tests/
uv run mypy src/
```

## 许可证

Apache License 2.0 - 见 [LICENSE](LICENSE)。第三方声明见 [NOTICE](NOTICE)。
