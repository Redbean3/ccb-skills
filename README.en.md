# CCB Skills

English | [简体中文](README.md)

A credit-card statement analysis skill for coding agents. CCB is short for Credit Card Bill. It helps Codex or Claude Code convert Chinese credit-card statement PDFs to Markdown, parse transaction details, exclude refunded purchases, and generate spending categories, daily spending trends, an interactive HTML dashboard, and CSV exports.

## Features

- Convert credit-card statement PDFs to Markdown with MarkItDown.
- Parse CMB-style sections: `还款`, `分期`, `退款`, and `消费`.
- Match equal-amount refunds to earlier positive transactions and exclude the matched charge from spending charts.
- Classify spending into categories such as `出行交通`, `食堂`, `其他饮食/食品商超`, `电商购物`, and more.
- Generate `report.html` and CSV exports; the HTML dashboard supports clicking the category pie chart and daily spending chart to filter one unified transaction detail table.

## Quickstart

```bash
npx inskills@latest add Redbean3/ccb-skills
```

Non-interactive installs:

```bash
npx inskills@latest add Redbean3/ccb-skills --codex
npx inskills@latest add Redbean3/ccb-skills --claude-code
npx inskills@latest add Redbean3/ccb-skills --all
```

After installation, ask your coding agent to use the skill:

```text
Use $ccb to analyze this credit-card statement PDF.
```

## Usage

The skill guides the agent through:

1. Converting the PDF with MarkItDown:

```bash
uvx --from 'markitdown[all]' markitdown statement.pdf -o statement.md
```

2. Running the bundled analyzer:

```bash
python3 scripts/analyze_cmb_credit_card_bill.py statement.md
```

3. Reviewing generated files, especially `report.html`.

## Outputs

| File | Description |
| --- | --- |
| `report.html` | Interactive HTML dashboard with summary cards, clickable charts, unified transaction details, search, sorting, and collapsible refund sections. |
| `transactions_parsed.csv` | Full parsed transaction export. |
| `transactions_cleaned.csv` | Cleaned spending export after matched-refund exclusions. |

## Repository Layout

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

## Resources

- [inskills](https://github.com/Redbean3/inskills) - install agent skills from GitHub repositories.
- [MarkItDown](https://github.com/microsoft/markitdown) - convert PDF, Office, HTML, and other files to Markdown.
- [uv](https://docs.astral.sh/uv/) - Python package and tool manager.

## Privacy

This repository intentionally contains no real statement PDFs, converted statement text, generated reports, or transaction data.

Do not commit real statement PDFs, converted Markdown files, generated HTML reports, or CSV exports. The `.gitignore` blocks common inputs and outputs created by this workflow.

## Requirements

- Python 3.11+ recommended for the analyzer.
- `uv` and MarkItDown for PDF conversion.
- No third-party Python packages are required by the analyzer itself.

## License

MIT
