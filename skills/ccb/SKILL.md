---
name: ccb
description: Analyze Chinese credit card statement PDFs, especially招商银行/CMB账单, by converting PDFs with uv/MarkItDown, parsing transaction details, matching and excluding refunded purchases, classifying spending, and generating an interactive HTML dashboard plus CSV transaction exports. Use when the user asks to analyze a credit-card bill,账单, PDF statement, refund-aware消费分类, spending pie chart, daily spending chart, or interactive bill report.
---

# CCB (Credit Card Bill)

Use this skill to turn a credit-card statement PDF or MarkItDown-generated Markdown into a local spending analysis. The bundled script is tuned for CMB/招商银行 credit-card statements like `招商银行信用卡对账单` with sections such as `还款`, `分期`, `退款`, and `消费`.

## Workflow

1. Locate the bill file in the user workspace.
2. If the input is a PDF, convert it to Markdown with uv/MarkItDown:

```bash
uvx --from 'markitdown[all]' markitdown 'statement.pdf' -o 'statement.md'
```

If `uv` is missing, install it only after approval. Prefer the official Astral installer or the user's existing package manager. If MarkItDown is already installed as a uv tool, `markitdown input.pdf -o output.md` is fine.

3. Run the bundled analyzer on the Markdown:

```bash
python3 scripts/analyze_cmb_credit_card_bill.py 'statement.md'
```

Use `--out <dir>` to choose the output directory and `--year YYYY` when the statement year cannot be inferred. The HTML report includes interactive detail filters for every category and spending date.

4. Open or summarize the generated report. Do not paste full sensitive transaction details into chat unless the user explicitly asks.

## Refund Handling

The analyzer uses this spending-analysis口径:

- Exclude repayments from消费分析.
- Treat positive `消费` and `分期` rows as charges.
- Treat negative `退款` and negative `分期` rows as refunds.
- Match a refund to an equal-amount positive charge whose transaction date is not later than the refund date.
- Exclude the matched positive charge from classification and charts.
- Keep unmatched refunds as账单调整, not as negative spending in a category.

This preserves both views:

- 分类分析合计: positive charges after matched-refund exclusions.
- 净账单口径: 分类分析合计 minus unmatched refunds, useful for checking against本期应还金额.

## Category Rules

The script applies deterministic keyword rules:

- `食堂` is its own category for canteen-like merchants.
- `滴滴`, `滴滴顺风车`, `滴滴出行`, `高德打车`, `交通`, `一卡通`, and similar travel keywords go to `出行交通`.
- Food, takeaway, drink, grocery, and supermarket-like merchants go to `其他饮食/食品商超`.
- Pinduoduo/Tmall/Taobao-like online purchases and named ecommerce merchants go to `电商购物`.
- Bilibili, iCloud, Lenovo-like merchants go to `数码娱乐/订阅`.
- SF Express, Hive Box, printing, and similar services go to `生活服务/物流`.
- Ambiguous personal or nickname merchants go to `其他/个人商户`.

If the user disputes a category, patch `CATEGORY_RULES` or the `categorize()` function in the bundled script and rerun it. Keep category changes explicit in the final response.

## Outputs

The script writes:

- `report.html`: interactive Chinese HTML dashboard with summary cards, clickable category/daily charts, one unified transaction detail table, search, sorting, and collapsible refund sections.
- `transactions_parsed.csv`: all parsed rows with section and exclusion metadata.
- `transactions_cleaned.csv`: positive spending rows after matched-refund exclusions.

## Validation

After script changes, run:

```bash
python3 -m py_compile scripts/analyze_cmb_credit_card_bill.py
```

When a statement contains a visible本期应还金额, sanity-check whether `净账单口径` equals or explains the statement balance.
