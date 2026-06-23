# CCB Skills

[English](README.en.md) | 简体中文

面向 Coding Agent 的信用卡账单分析 skill。CCB 是 Credit Card Bill 的缩写，表示信用卡账单。它帮助 Codex 或 Claude Code 将中文信用卡 PDF 账单转换为 Markdown，解析交易明细，剔除已退款对应账目，并生成消费分类、每日消费趋势、交互式 HTML 仪表盘和 CSV 明细。

## 功能

- 使用 MarkItDown 将信用卡账单 PDF 转换为 Markdown。
- 解析 CMB/招商银行风格的账单分区：`还款`、`分期`、`退款`、`消费`。
- 将同金额退款匹配到更早的正向交易，并从消费图表中剔除对应消费。
- 将消费归类到 `出行交通`、`食堂`、`其他饮食/食品商超`、`电商购物` 等类别。
- 生成 `report.html` 和 CSV 明细；HTML 仪表盘支持点击分类饼图和每日消费图表筛选同一张消费明细表。

## Quickstart

```bash
npx inskills@latest add Redbean3/ccb-skills
```

也可以非交互安装：

```bash
npx inskills@latest add Redbean3/ccb-skills --codex
npx inskills@latest add Redbean3/ccb-skills --claude-code
npx inskills@latest add Redbean3/ccb-skills --all
```

安装后，让你的 coding agent 使用该 skill：

```text
Use $ccb to analyze this credit-card statement PDF.
```

## 使用

该 skill 会引导 agent 完成：

1. 使用 MarkItDown 转换 PDF：

```bash
uvx --from 'markitdown[all]' markitdown statement.pdf -o statement.md
```

2. 运行内置分析脚本：

```bash
python3 scripts/analyze_cmb_credit_card_bill.py statement.md
```

3. 查看生成文件，尤其是 `report.html`。

## 输出文件

| 文件 | 说明 |
| --- | --- |
| `report.html` | 交互式 HTML 仪表盘，包含指标卡、可点击图表、统一消费明细、搜索、排序和可折叠退款分区。 |
| `transactions_parsed.csv` | 完整解析明细。 |
| `transactions_cleaned.csv` | 剔除已匹配退款后的消费明细。 |

## 仓库结构

```text
.
└── skills/
    └── ccb/
        ├── SKILL.md
        ├── agents/
        │   └── openai.yaml
        └── scripts/
            └── analyze_cmb_credit_card_bill.py
```

## 相关资源

- [inskills](https://github.com/Redbean3/inskills) - 从 GitHub 仓库安装 agent skills。
- [MarkItDown](https://github.com/microsoft/markitdown) - 将 PDF、Office、HTML 等文件转换为 Markdown。
- [uv](https://docs.astral.sh/uv/) - Python 包和工具管理器。

## 隐私

本仓库不包含任何真实账单 PDF、转换后的账单文本、分析报告或交易数据。

不要提交真实账单 PDF、转换后的 Markdown、生成的 HTML 报告或 CSV 明细。仓库里的 `.gitignore` 已屏蔽该工作流常见的输入和输出文件。

## 环境要求

- 推荐 Python 3.11+ 运行分析脚本。
- PDF 转换需要 `uv` 和 MarkItDown。
- 分析脚本本身不依赖第三方 Python 包。

## License

MIT
