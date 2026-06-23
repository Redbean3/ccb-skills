# Credit Card Bill Analysis Skills

一个面向 Coding Agent 的信用卡账单分析 skill。它可以指导 agent 使用 `uv` + MarkItDown 将中文信用卡 PDF 账单转换成 Markdown，再解析招商银行/CMB 账单明细，剔除已退款对应账目，生成消费分类、每日消费趋势、交互式 HTML 报告和 CSV 明细。

> This repository intentionally contains no real statement PDFs, converted bills, reports, or transaction data.

## What It Does

- Converts credit-card statement PDFs to Markdown with MarkItDown.
- Parses CMB/招商银行-style sections: `还款`, `分期`, `退款`, `消费`.
- Matches equal-amount refunds to earlier positive transactions and excludes the matched charge from spending charts.
- Classifies spending into categories such as `出行交通`, `食堂`, `其他饮食/食品商超`, `电商购物`, and more.
- Generates:
  - `report.html`: interactive HTML report with summary cards, charts, sortable tables, category detail tabs, search, and collapsible refund sections.
  - `report.md`: Markdown report.
  - `category_pie.svg`: category pie chart.
  - `daily_spending.svg`: daily spending bar/line chart.
  - `transactions_parsed.csv` and `transactions_cleaned.csv`.

## Install

Clone the repository and run the setup script:

```bash
git clone git@github.com:Redbean3/credit-card-bill-analysis-skills.git
cd credit-card-bill-analysis-skills
./setup
```

The installer asks:

```text
Which coding agents do you want to install this skill on?
  1) Codex
  2) Claude Code
  3) Both
  4) Cancel
```

Non-interactive options:

```bash
./setup --codex
./setup --claude-code
./setup --all
./setup --all --force
./setup --all --dry-run
```

Default install targets:

- Codex: `${CODEX_HOME:-~/.codex}/skills/credit-card-bill-analysis`
- Claude Code: `${CLAUDE_HOME:-~/.claude}/skills/credit-card-bill-analysis`

You can override exact target directories:

```bash
./setup --codex --codex-dir ~/.codex/skills/credit-card-bill-analysis
./setup --claude-code --claude-dir ~/.claude/skills/credit-card-bill-analysis
```

## Use

After installation, ask your coding agent something like:

```text
Use $credit-card-bill-analysis to analyze this credit-card statement PDF.
```

The skill will guide the agent through:

1. Converting the PDF with MarkItDown:

```bash
uvx --from 'markitdown[all]' markitdown statement.pdf -o statement.md
```

2. Running the bundled analyzer:

```bash
python3 scripts/analyze_cmb_credit_card_bill.py statement.md
```

3. Reviewing generated files, especially `report.html`.

## Repository Layout

```text
.
├── setup
└── skills/
    └── credit-card-bill-analysis/
        ├── SKILL.md
        ├── agents/
        │   └── openai.yaml
        └── scripts/
            └── analyze_cmb_credit_card_bill.py
```

`agents/openai.yaml` is Codex UI metadata. The installer omits that directory when installing for Claude Code.

## Privacy

Do not commit statement PDFs, converted Markdown files, generated reports, or CSV exports. The `.gitignore` blocks the common outputs created by this workflow.

## Requirements

- Python 3.11+ recommended for the analyzer.
- `uv` and MarkItDown for PDF conversion.
- No Python package dependencies are required by the analyzer itself.

## License

MIT
