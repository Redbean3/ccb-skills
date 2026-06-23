#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path


SECTION_NAMES = {"还款", "分期", "退款", "消费"}

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("食堂", ("食堂",)),
    ("出行交通", ("滴滴", "高德打车", "打车", "交通", "一卡通")),
    ("分期还款", ("消费分期",)),
    (
        "其他饮食/食品商超",
        (
            "卤菜",
            "果仁",
            "食品",
            "酒业",
            "饮品",
            "外卖",
            "生鲜",
            "商超",
            "超市",
            "购物中心",
            "商贸",
        ),
    ),
    (
        "电商购物",
        (
            "拼多多",
            "天猫",
            "淘宝",
            "阿里",
            "电商",
            "电子商务",
            "平台商户",
            "百货",
            "眼镜",
            "纺织",
            "运动",
            "玩偶",
            "科技",
            "实业",
        ),
    ),
    ("数码娱乐/订阅", ("哔哩哔哩", "iCloud", "联想")),
    ("生活服务/物流", ("快递", "速运", "物流", "图文", "打印", "寄件")),
)


@dataclass
class Transaction:
    idx: int
    section: str
    trans_date: date
    post_date: date | None
    description: str
    amount: Decimal
    card: str
    original: str
    excluded: bool = False
    exclude_reason: str = ""
    category: str = ""


def money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def infer_year(markdown: str, fallback: int | None) -> int:
    if fallback:
        return fallback
    patterns = (
        r"CMB Credit Card Statement \((\d{4})\.\d{2}\)",
        r"信用卡对账单.*?(\d{4})年\d{2}月",
        r"Statement Date.*?(\d{4})",
    )
    for pattern in patterns:
        match = re.search(pattern, markdown, flags=re.S)
        if match:
            return int(match.group(1))
    raise SystemExit("Could not infer statement year. Re-run with --year YYYY.")


def infer_title(markdown: str, input_path: Path) -> str:
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if "信用卡对账单" in line or "Credit Card Statement" in line:
            return line
    return input_path.stem


def parse_amount(text: str) -> Decimal | None:
    cleaned = (
        text.replace("\xa0", " ")
        .replace("¥", "")
        .replace(",", "")
        .replace("(CN)", "")
        .strip()
    )
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", cleaned):
        return None
    return Decimal(cleaned)


def parse_date_cell(text: str, year: int) -> tuple[date, date | None] | None:
    matches = re.findall(r"(\d{2})/(\d{2})", text)
    if not matches:
        return None
    dates = [date(year, int(month), int(day)) for month, day in matches]
    return dates[0], dates[1] if len(dates) > 1 else None


def looks_like_separator(line: str) -> bool:
    chars = set(line.replace("|", "").replace("-", "").replace(" ", "").strip())
    return not chars


def split_cells(line: str) -> list[str]:
    return [cell.replace("\xa0", " ").strip() for cell in line.strip().strip("|").split("|")]


def should_keep_pending_description(line: str) -> bool:
    text = line.strip()
    prefixes = (
        "财付通-",
        "支付宝-",
        "拼多多支付-",
        "抖音支付-",
        "微信支付-",
        "消费分期-",
    )
    return text.startswith(prefixes)


def parse_transactions(markdown: str, year: int) -> list[Transaction]:
    section = ""
    pending_description = ""
    transactions: list[Transaction] = []

    for raw_line in markdown.splitlines():
        line = raw_line.replace("\xa0", " ").rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped in SECTION_NAMES:
            section = stripped
            pending_description = ""
            continue

        if stripped.startswith("|"):
            if looks_like_separator(stripped):
                continue
            cells = split_cells(stripped)
            if not cells:
                continue

            parsed_dates = parse_date_cell(cells[0], year)
            if parsed_dates is None:
                continue

            card_index = next(
                (i for i, cell in enumerate(cells) if re.fullmatch(r"\d{4}", cell)),
                None,
            )
            if card_index is None:
                continue

            amount_index = None
            amount = None
            for i in range(card_index - 1, 0, -1):
                candidate = parse_amount(cells[i])
                if candidate is not None:
                    amount_index = i
                    amount = candidate
                    break
            if amount_index is None or amount is None:
                continue

            description_parts = [cell for cell in cells[1:amount_index] if cell]
            description = " ".join(description_parts).strip() or pending_description
            description = re.sub(r"\s+", " ", description).strip() or "未知商户"
            trans_date, post_date = parsed_dates
            transactions.append(
                Transaction(
                    idx=len(transactions) + 1,
                    section=section,
                    trans_date=trans_date,
                    post_date=post_date,
                    description=description,
                    amount=amount,
                    card=cells[card_index],
                    original=cells[-1] if cells else "",
                )
            )
            pending_description = ""
            continue

        if should_keep_pending_description(stripped):
            pending_description = stripped

    return transactions


def categorize(description: str) -> str:
    for category, keywords in CATEGORY_RULES:
        if any(keyword in description for keyword in keywords):
            return category
    return "其他/个人商户"


def match_refunds(transactions: list[Transaction]) -> tuple[list[tuple[Transaction, Transaction]], list[Transaction]]:
    positives = [
        tx
        for tx in transactions
        if tx.amount > 0 and tx.section in {"消费", "分期"}
    ]
    refunds = [
        tx
        for tx in transactions
        if tx.amount < 0 and tx.section in {"退款", "分期"}
    ]
    matched: list[tuple[Transaction, Transaction]] = []
    unmatched_refunds: list[Transaction] = []
    used_positive_ids: set[int] = set()

    for refund in refunds:
        refund_abs = -refund.amount
        candidates = [
            tx
            for tx in positives
            if tx.idx not in used_positive_ids
            and tx.amount == refund_abs
            and tx.trans_date <= refund.trans_date
        ]
        if not candidates:
            unmatched_refunds.append(refund)
            continue

        candidates.sort(
            key=lambda tx: (
                abs((refund.trans_date - tx.trans_date).days),
                0 if tx.section == refund.section else 1,
                tx.idx,
            )
        )
        positive = candidates[0]
        used_positive_ids.add(positive.idx)
        positive.excluded = True
        positive.exclude_reason = f"已由 {refund.trans_date.isoformat()} 退款抵扣"
        refund.excluded = True
        refund.exclude_reason = f"抵扣 {positive.trans_date.isoformat()} 正向交易"
        matched.append((refund, positive))

    return matched, unmatched_refunds


def cleaned_spending(transactions: list[Transaction]) -> list[Transaction]:
    result = [
        tx
        for tx in transactions
        if tx.section in {"消费", "分期"}
        and tx.amount > 0
        and not tx.excluded
    ]
    for tx in result:
        tx.category = categorize(tx.description)
    return result


def aggregate_by_category(transactions: list[Transaction]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for tx in transactions:
        totals[tx.category] += tx.amount
    return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))


def aggregate_by_date(transactions: list[Transaction]) -> dict[date, Decimal]:
    totals: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for tx in transactions:
        totals[tx.trans_date] += tx.amount
    if not totals:
        return {}
    start = min(totals)
    end = max(totals)
    current = start
    dense: dict[date, Decimal] = {}
    while current <= end:
        dense[current] = totals[current]
        current += timedelta(days=1)
    return dense


def pie_slice_path(cx: float, cy: float, radius: float, start: float, end: float) -> str:
    start_x = cx + radius * math.cos(start)
    start_y = cy + radius * math.sin(start)
    end_x = cx + radius * math.cos(end)
    end_y = cy + radius * math.sin(end)
    large_arc = 1 if end - start > math.pi else 0
    return (
        f"M {cx:.2f} {cy:.2f} "
        f"L {start_x:.2f} {start_y:.2f} "
        f"A {radius:.2f} {radius:.2f} 0 {large_arc} 1 {end_x:.2f} {end_y:.2f} Z"
    )


def write_pie_svg(category_totals: dict[str, Decimal], path: Path) -> None:
    width, height = 920, 620
    cx, cy, radius = 295, 330, 205
    colors = [
        "#4E79A7",
        "#F28E2B",
        "#E15759",
        "#76B7B2",
        "#59A14F",
        "#EDC948",
        "#B07AA1",
        "#FF9DA7",
        "#9C755F",
        "#BAB0AC",
    ]
    total = sum(category_totals.values(), Decimal("0"))
    start = -math.pi / 2
    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="40" y="55" font-size="28" font-family="Arial, sans-serif" font-weight="700" fill="#1f2933">本期消费分类饼图</text>',
        f'<text x="40" y="90" font-size="16" font-family="Arial, sans-serif" fill="#52606d">剔除已匹配退款后的消费合计：¥{money(total)}</text>',
    ]
    for index, (category, amount) in enumerate(category_totals.items()):
        fraction = float(amount / total) if total else 0
        end = start + fraction * math.tau
        svg.append(
            f'<path d="{pie_slice_path(cx, cy, radius, start, end)}" '
            f'fill="{colors[index % len(colors)]}" stroke="#ffffff" stroke-width="2"/>'
        )
        start = end

    legend_x = 560
    legend_y = 150
    for index, (category, amount) in enumerate(category_totals.items()):
        pct = float(amount / total * Decimal("100")) if total else 0
        y = legend_y + index * 46
        svg.extend(
            [
                f'<rect x="{legend_x}" y="{y - 16}" width="18" height="18" rx="3" fill="{colors[index % len(colors)]}"/>',
                f'<text x="{legend_x + 30}" y="{y}" font-size="16" font-family="Arial, sans-serif" fill="#1f2933">{escape(category)}</text>',
                f'<text x="{legend_x + 30}" y="{y + 21}" font-size="14" font-family="Arial, sans-serif" fill="#52606d">¥{money(amount)} · {pct:.1f}%</text>',
            ]
        )
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def write_daily_svg(daily_totals: dict[date, Decimal], path: Path) -> None:
    width, height = 1160, 560
    margin_left, margin_right = 78, 42
    margin_top, margin_bottom = 90, 92
    chart_width = width - margin_left - margin_right
    chart_height = height - margin_top - margin_bottom
    max_amount = max(daily_totals.values(), default=Decimal("0"))
    y_max = max(Decimal("10"), (max_amount * Decimal("1.15")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    items = list(daily_totals.items())
    count = len(items)
    step = chart_width / max(count, 1)
    bar_width = min(22, step * 0.68)

    def x_at(i: int) -> float:
        return margin_left + step * i + step / 2

    def y_at(amount: Decimal) -> float:
        return margin_top + chart_height - (float(amount) / float(y_max)) * chart_height

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="40" y="52" font-size="28" font-family="Arial, sans-serif" font-weight="700" fill="#1f2933">每日消费金额</text>',
        '<text x="40" y="80" font-size="16" font-family="Arial, sans-serif" fill="#52606d">按交易日汇总，已剔除可匹配退款对应账目</text>',
    ]

    for tick in range(6):
        amount = y_max * Decimal(tick) / Decimal(5)
        y = y_at(amount)
        svg.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" stroke="#e4e7eb" stroke-width="1"/>')
        svg.append(f'<text x="{margin_left - 12}" y="{y + 5:.2f}" font-size="12" font-family="Arial, sans-serif" text-anchor="end" fill="#52606d">{money(amount)}</text>')

    line_points: list[str] = []
    for index, (day, amount) in enumerate(items):
        x = x_at(index)
        y = y_at(amount)
        bar_height = margin_top + chart_height - y
        svg.append(
            f'<rect x="{x - bar_width / 2:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" '
            'fill="#4E79A7" opacity="0.82"/>'
        )
        line_points.append(f"{x:.2f},{y:.2f}")
        if index % 2 == 0 or index == count - 1:
            svg.append(
                f'<text x="{x:.2f}" y="{height - 52}" font-size="12" font-family="Arial, sans-serif" '
                f'text-anchor="middle" fill="#52606d" transform="rotate(-35 {x:.2f} {height - 52})">{day.strftime("%m/%d")}</text>'
            )

    if line_points:
        svg.append(
            f'<polyline points="{" ".join(line_points)}" fill="none" stroke="#E15759" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for point in line_points:
            x, y = point.split(",")
            svg.append(f'<circle cx="{x}" cy="{y}" r="3.2" fill="#E15759" stroke="#ffffff" stroke-width="1.5"/>')

    svg.extend(
        [
            f'<line x1="{margin_left}" y1="{margin_top + chart_height}" x2="{width - margin_right}" y2="{margin_top + chart_height}" stroke="#9aa5b1"/>',
            f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + chart_height}" stroke="#9aa5b1"/>',
            '<rect x="920" y="36" width="18" height="12" fill="#4E79A7" opacity="0.82"/>',
            '<text x="945" y="47" font-size="14" font-family="Arial, sans-serif" fill="#52606d">柱：日消费额</text>',
            '<line x1="920" y1="70" x2="938" y2="70" stroke="#E15759" stroke-width="3"/>',
            '<text x="945" y="75" font-size="14" font-family="Arial, sans-serif" fill="#52606d">线：趋势</text>',
            "</svg>",
        ]
    )
    path.write_text("\n".join(svg), encoding="utf-8")


def write_csv(transactions: list[Transaction], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["交易日", "记账日", "分区", "商户", "金额", "分类", "是否剔除", "剔除原因"])
        for tx in transactions:
            writer.writerow(
                [
                    tx.trans_date.isoformat(),
                    tx.post_date.isoformat() if tx.post_date else "",
                    tx.section,
                    tx.description,
                    money(tx.amount),
                    tx.category,
                    "是" if tx.excluded else "否",
                    tx.exclude_reason,
                ]
            )


def write_report(
    title: str,
    transactions: list[Transaction],
    cleaned: list[Transaction],
    matched: list[tuple[Transaction, Transaction]],
    unmatched_refunds: list[Transaction],
    category_totals: dict[str, Decimal],
    daily_totals: dict[date, Decimal],
    path: Path,
    top_categories_count: int,
) -> None:
    gross_positive = sum(
        (tx.amount for tx in transactions if tx.section in {"消费", "分期"} and tx.amount > 0),
        Decimal("0"),
    )
    matched_refund_total = sum((-refund.amount for refund, _ in matched), Decimal("0"))
    unmatched_refund_total = sum((-tx.amount for tx in unmatched_refunds), Decimal("0"))
    cleaned_total = sum((tx.amount for tx in cleaned), Decimal("0"))
    net_after_unmatched_refunds = cleaned_total - unmatched_refund_total
    top_day = None
    top_day_amount = Decimal("0")
    if daily_totals:
        top_day, top_day_amount = max(daily_totals.items(), key=lambda item: item[1])

    lines: list[str] = [
        f"# {title}分析",
        "",
        "## 分析口径",
        "",
        "- 还款记录不计入消费分析。",
        "- 正向消费与本期可匹配的同金额退款成对剔除；匹配原则是退款金额相同，且正向交易日不晚于退款交易日。",
        "- 未在本期找到对应正向交易的退款不归入任何消费分类，仅作为账单调整列示。",
        "",
        "## 总览",
        "",
        f"- 本期正向入账消费/分期合计：¥{money(gross_positive)}",
        f"- 已匹配并剔除的退款对应消费：¥{money(matched_refund_total)}，共 {len(matched)} 笔",
        f"- 剔除退款后用于分类分析的消费合计：¥{money(cleaned_total)}",
        f"- 未匹配到本期正向交易的退款：¥{money(unmatched_refund_total)}，共 {len(unmatched_refunds)} 笔",
        f"- 若再扣除未匹配退款，本期净账单口径金额：¥{money(net_after_unmatched_refunds)}",
    ]
    if top_day:
        lines.append(f"- 单日消费最高：{top_day.strftime('%m/%d')}，¥{money(top_day_amount)}")

    lines.extend(
        [
            "",
            "## 消费分类",
            "",
            "![本期消费分类饼图](category_pie.svg)",
            "",
            "| 分类 | 金额 | 占比 | 笔数 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for category, amount in category_totals.items():
        count = sum(1 for tx in cleaned if tx.category == category)
        pct = amount / cleaned_total * Decimal("100") if cleaned_total else Decimal("0")
        lines.append(f"| {category} | ¥{money(amount)} | {pct.quantize(Decimal('0.1'))}% | {count} |")

    top_categories = list(category_totals.items())[:top_categories_count]
    if top_categories:
        lines.extend(
            [
                "",
                f"## 金额前{len(top_categories)}类目的具体消费明细",
                "",
                "以下明细均已剔除可匹配退款对应账目。",
                "",
            ]
        )
        for category, amount in top_categories:
            category_transactions = [tx for tx in cleaned if tx.category == category]
            lines.extend(
                [
                    f"### {category}（¥{money(amount)}，{len(category_transactions)} 笔）",
                    "",
                    "| 交易日 | 商户 | 金额 |",
                    "| --- | --- | ---: |",
                ]
            )
            for tx in category_transactions:
                lines.append(f"| {tx.trans_date.strftime('%m/%d')} | {tx.description} | ¥{money(tx.amount)} |")
            lines.append("")

    lines.extend(
        [
            "",
            "## 每日消费",
            "",
            "![每日消费金额](daily_spending.svg)",
            "",
            "| 日期 | 金额 |",
            "| --- | ---: |",
        ]
    )
    for day, amount in daily_totals.items():
        if amount:
            lines.append(f"| {day.strftime('%m/%d')} | ¥{money(amount)} |")

    lines.extend(
        [
            "",
            "## 已剔除的退款对应账目",
            "",
            "| 退款交易日 | 退款金额 | 被剔除正向交易日 | 被剔除商户 |",
            "| --- | ---: | --- | --- |",
        ]
    )
    for refund, positive in matched:
        lines.append(
            f"| {refund.trans_date.strftime('%m/%d')} | ¥{money(-refund.amount)} | "
            f"{positive.trans_date.strftime('%m/%d')} | {positive.description} |"
        )

    lines.extend(["", "## 未匹配到本期正向交易的退款", ""])
    if unmatched_refunds:
        lines.extend(["| 退款交易日 | 摘要 | 金额 |", "| --- | --- | ---: |"])
        for tx in unmatched_refunds:
            lines.append(f"| {tx.trans_date.strftime('%m/%d')} | {tx.description} | ¥{money(-tx.amount)} |")
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## 分类判断说明",
            "",
            "- 出行交通：滴滴顺风车、滴滴出行、高德打车、交通、一卡通。",
            "- 食堂：账单中所有包含“食堂”的商户。",
            "- 其他饮食/食品商超：外卖、餐饮、食品品牌、饮品、盒马及生活超市类商户。",
            "- 电商购物：拼多多、天猫、阿里、服饰/运动/眼镜/百货等线上购物商户。",
            "- 其他/个人商户：账单摘要无法稳定判断品类的个人或昵称类商户。",
        ]
    )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def html_money(value: Decimal) -> str:
    return f"¥{money(value)}"


def write_interactive_html(
    title: str,
    transactions: list[Transaction],
    cleaned: list[Transaction],
    matched: list[tuple[Transaction, Transaction]],
    unmatched_refunds: list[Transaction],
    category_totals: dict[str, Decimal],
    daily_totals: dict[date, Decimal],
    path: Path,
    top_categories_count: int,
) -> None:
    gross_positive = sum(
        (tx.amount for tx in transactions if tx.section in {"消费", "分期"} and tx.amount > 0),
        Decimal("0"),
    )
    matched_refund_total = sum((-refund.amount for refund, _ in matched), Decimal("0"))
    unmatched_refund_total = sum((-tx.amount for tx in unmatched_refunds), Decimal("0"))
    cleaned_total = sum((tx.amount for tx in cleaned), Decimal("0"))
    net_after_unmatched_refunds = cleaned_total - unmatched_refund_total
    top_day = None
    top_day_amount = Decimal("0")
    if daily_totals:
        top_day, top_day_amount = max(daily_totals.items(), key=lambda item: item[1])
    max_daily = max(daily_totals.values(), default=Decimal("1"))
    top_categories = list(category_totals.items())[:top_categories_count]

    category_rows: list[str] = []
    for category, amount in category_totals.items():
        count = sum(1 for tx in cleaned if tx.category == category)
        pct = amount / cleaned_total * Decimal("100") if cleaned_total else Decimal("0")
        category_rows.append(
            "<tr>"
            f"<td>{escape(category)}</td>"
            f'<td data-value="{amount}">{html_money(amount)}</td>'
            f'<td data-value="{pct}">{pct.quantize(Decimal("0.1"))}%</td>'
            f'<td data-value="{count}">{count}</td>'
            "</tr>"
        )

    daily_rows: list[str] = []
    for day, amount in daily_totals.items():
        if not amount:
            continue
        width = float(amount / max_daily * Decimal("100")) if max_daily else 0
        daily_rows.append(
            "<tr>"
            f'<td data-value="{day.isoformat()}">{day.strftime("%m/%d")}</td>'
            f'<td data-value="{amount}">{html_money(amount)}</td>'
            f'<td><span class="bar" style="width:{width:.1f}%"></span></td>'
            "</tr>"
        )

    matched_rows = [
        "<tr>"
        f'<td data-value="{refund.trans_date.isoformat()}">{refund.trans_date.strftime("%m/%d")}</td>'
        f'<td data-value="{-refund.amount}">{html_money(-refund.amount)}</td>'
        f'<td data-value="{positive.trans_date.isoformat()}">{positive.trans_date.strftime("%m/%d")}</td>'
        f"<td>{escape(positive.description)}</td>"
        "</tr>"
        for refund, positive in matched
    ]
    unmatched_rows = [
        "<tr>"
        f'<td data-value="{tx.trans_date.isoformat()}">{tx.trans_date.strftime("%m/%d")}</td>'
        f"<td>{escape(tx.description)}</td>"
        f'<td data-value="{-tx.amount}">{html_money(-tx.amount)}</td>'
        "</tr>"
        for tx in unmatched_refunds
    ]

    detail_data = {
        category: {
            "amount": html_money(amount),
            "count": sum(1 for tx in cleaned if tx.category == category),
            "rows": [
                {
                    "date": tx.trans_date.strftime("%m/%d"),
                    "dateValue": tx.trans_date.isoformat(),
                    "merchant": tx.description,
                    "amount": html_money(tx.amount),
                    "amountValue": float(tx.amount),
                }
                for tx in cleaned
                if tx.category == category
            ],
        }
        for category, amount in top_categories
    }
    tab_buttons = "\n".join(
        f'<button type="button" class="tab" data-category="{escape(category, quote=True)}">'
        f"{escape(category)}<span>{html_money(amount)}</span></button>"
        for category, amount in top_categories
    )
    top_day_label = top_day.strftime("%m/%d") if top_day else "-"

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}分析</title>
  <style>
    :root {{ --ink:#172026; --muted:#5f6f7a; --line:#d7dee3; --paper:#fff; --bg:#eef2f4; --blue:#2f6f9f; --green:#2f855a; --red:#b84a4a; --gold:#a56b18; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.55; }}
    .nav {{ position:sticky; top:0; z-index:10; display:flex; gap:10px; overflow-x:auto; padding:12px max(18px,calc((100vw - 1180px)/2 + 22px)); background:rgba(255,255,255,.94); border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }}
    .nav a {{ color:var(--muted); text-decoration:none; white-space:nowrap; font-size:14px; padding:6px 9px; border-radius:6px; }}
    .nav a:hover {{ background:#edf4f8; color:var(--blue); }}
    header, main {{ max-width:1180px; margin:0 auto; padding:0 22px; }}
    header {{ padding-top:38px; padding-bottom:20px; }}
    h1 {{ margin:0; font-size:34px; line-height:1.18; letter-spacing:0; }}
    .subtitle {{ color:var(--muted); max-width:820px; }}
    section {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; margin:18px 0; box-shadow:0 16px 42px rgba(24,39,75,.08); overflow:hidden; }}
    .head {{ padding:22px 24px 16px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0; font-size:22px; letter-spacing:0; }}
    .note {{ color:var(--muted); margin:6px 0 0; font-size:14px; }}
    .body {{ padding:22px 24px 26px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }}
    .metric {{ border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:8px; padding:16px; background:#fbfcfd; min-height:112px; }}
    .metric.refund {{ border-left-color:var(--red); }} .metric.net {{ border-left-color:var(--green); }} .metric.day {{ border-left-color:var(--gold); }}
    .label {{ color:var(--muted); font-size:13px; }} .value {{ margin-top:10px; font-size:25px; font-weight:750; }} .sub {{ margin-top:5px; color:var(--muted); font-size:13px; }}
    .charts {{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.2fr); gap:18px; }}
    .chart {{ border:1px solid var(--line); border-radius:8px; background:#fff; overflow:auto; min-height:360px; }}
    .chart img {{ display:block; width:100%; min-width:520px; height:auto; }}
    .wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ border-bottom:1px solid #e6eaed; padding:10px 12px; text-align:left; vertical-align:top; }}
    th {{ color:#344854; background:#f7f9fa; font-weight:700; white-space:nowrap; user-select:none; }}
    th[data-sort] {{ cursor:pointer; }} td[data-value], .num {{ text-align:right; }}
    .tabs {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:14px; }}
    .tab {{ border:1px solid var(--line); background:#fff; border-radius:8px; padding:9px 12px; font:inherit; cursor:pointer; display:inline-flex; gap:8px; }}
    .tab span {{ color:var(--muted); font-size:13px; }} .tab.active {{ border-color:var(--blue); background:#edf7fc; color:var(--blue); }}
    .toolbar {{ display:flex; justify-content:space-between; gap:12px; align-items:center; margin:10px 0 14px; flex-wrap:wrap; }}
    .search {{ min-width:260px; border:1px solid var(--line); border-radius:8px; padding:9px 11px; font:inherit; }}
    .detail-title {{ margin:0; font-size:18px; font-weight:750; }} .detail-meta {{ color:var(--muted); font-size:14px; }}
    .bar {{ display:block; height:10px; border-radius:999px; min-width:3px; background:linear-gradient(90deg,#2f6f9f,#6aa875); }}
    details {{ border:1px solid var(--line); border-radius:8px; background:#fbfcfd; margin-top:14px; }}
    summary {{ cursor:pointer; padding:13px 15px; font-weight:700; }} .details-body {{ padding:0 15px 15px; }}
    .callout {{ border-left:4px solid var(--gold); background:#fff8e8; padding:12px 14px; border-radius:8px; color:#5d4218; margin:0 0 16px; }}
    .links {{ display:flex; gap:10px; flex-wrap:wrap; }} .links a {{ color:var(--blue); border:1px solid var(--line); border-radius:8px; padding:8px 10px; text-decoration:none; background:#fff; }}
    @media (max-width:900px) {{ h1 {{ font-size:28px; }} .metrics,.charts {{ grid-template-columns:1fr; }} }}
    @media (max-width:560px) {{ header,main {{ padding-left:14px; padding-right:14px; }} .body,.head {{ padding-left:16px; padding-right:16px; }} .value {{ font-size:21px; }} table {{ font-size:13px; }} }}
  </style>
</head>
<body>
  <nav class="nav"><a href="#overview">总览</a><a href="#charts">图表</a><a href="#categories">分类</a><a href="#top-details">前{len(top_categories)}明细</a><a href="#daily">每日</a><a href="#refunds">退款</a><a href="#rules">口径</a></nav>
  <header><h1>{escape(title)}分析</h1><p class="subtitle">基于 MarkItDown 转换后的账单明细生成；已剔除本期可匹配退款对应账目，保留未匹配退款作为账单调整。</p></header>
  <main>
    <section id="overview"><div class="head"><h2>总览</h2><p class="note">金额口径同时展示分类分析口径与账单净额校验口径。</p></div><div class="body"><div class="metrics">
      <div class="metric"><div class="label">正向入账消费/分期</div><div class="value">{html_money(gross_positive)}</div><div class="sub">退款剔除前总额</div></div>
      <div class="metric refund"><div class="label">已匹配退款剔除</div><div class="value">{html_money(matched_refund_total)}</div><div class="sub">{len(matched)} 笔对应账目</div></div>
      <div class="metric net"><div class="label">分类分析消费合计</div><div class="value">{html_money(cleaned_total)}</div><div class="sub">{len(cleaned)} 笔有效消费</div></div>
      <div class="metric day"><div class="label">单日消费最高</div><div class="value">{top_day_label}</div><div class="sub">{html_money(top_day_amount)}</div></div>
    </div></div></section>
    <section id="charts"><div class="head"><h2>图表</h2><p class="note">分类饼图与每日消费趋势图。</p></div><div class="body"><div class="charts"><div class="chart"><img src="category_pie.svg" alt="本期消费分类饼图"></div><div class="chart"><img src="daily_spending.svg" alt="每日消费金额图"></div></div></div></section>
    <section id="categories"><div class="head"><h2>消费分类</h2><p class="note">点击表头可排序。</p></div><div class="body"><div class="wrap"><table class="sortable"><thead><tr><th data-sort="text">分类</th><th class="num" data-sort="number">金额</th><th class="num" data-sort="number">占比</th><th class="num" data-sort="number">笔数</th></tr></thead><tbody>{"".join(category_rows)}</tbody></table></div></div></section>
    <section id="top-details"><div class="head"><h2>金额前{len(top_categories)}类目明细</h2><p class="note">只展示剔除可匹配退款后的有效消费。</p></div><div class="body"><div class="tabs">{tab_buttons}</div><div class="toolbar"><div><p class="detail-title" id="detail-title"></p><div class="detail-meta" id="detail-meta"></div></div><input class="search" id="detail-search" type="search" placeholder="搜索商户或日期"></div><div class="wrap"><table class="sortable" id="detail-table"><thead><tr><th data-sort="text">交易日</th><th data-sort="text">商户</th><th class="num" data-sort="number">金额</th></tr></thead><tbody></tbody></table></div></div></section>
    <section id="daily"><div class="head"><h2>每日消费</h2><p class="note">按交易日汇总，右侧条形用于比较单日金额。</p></div><div class="body"><div class="wrap"><table class="sortable"><thead><tr><th data-sort="text">日期</th><th class="num" data-sort="number">金额</th><th>强度</th></tr></thead><tbody>{"".join(daily_rows)}</tbody></table></div></div></section>
    <section id="refunds"><div class="head"><h2>退款处理</h2><p class="note">已匹配退款从消费分类和图表中剔除；未匹配退款仅用于净额校验。</p></div><div class="body"><p class="callout">若再扣除未匹配退款 {html_money(unmatched_refund_total)}，本期净账单口径金额为 {html_money(net_after_unmatched_refunds)}。</p><details open><summary>已剔除的退款对应账目（{len(matched)} 笔）</summary><div class="details-body wrap"><table class="sortable"><thead><tr><th data-sort="text">退款交易日</th><th class="num" data-sort="number">退款金额</th><th data-sort="text">被剔除交易日</th><th data-sort="text">被剔除商户</th></tr></thead><tbody>{"".join(matched_rows)}</tbody></table></div></details><details><summary>未匹配到本期正向交易的退款（{len(unmatched_refunds)} 笔）</summary><div class="details-body wrap"><table class="sortable"><thead><tr><th data-sort="text">退款交易日</th><th data-sort="text">摘要</th><th class="num" data-sort="number">金额</th></tr></thead><tbody>{"".join(unmatched_rows) if unmatched_rows else '<tr><td colspan="3">无</td></tr>'}</tbody></table></div></details></div></section>
    <section id="rules"><div class="head"><h2>分析口径与文件</h2><p class="note">分类规则和导出文件。</p></div><div class="body"><ul><li>还款记录不计入消费分析。</li><li>正向消费与同金额、交易日不晚于退款日的退款成对剔除。</li><li>未匹配退款不归入任何消费分类，仅作为账单调整。</li><li>出行交通包含滴滴顺风车、滴滴出行、高德打车、交通、一卡通等。</li><li>食堂单独成类，其他餐饮、食品、商超归入其他饮食/食品商超。</li></ul><div class="links"><a href="report.md">Markdown 报告</a><a href="transactions_cleaned.csv">清洗后消费明细</a><a href="transactions_parsed.csv">完整解析明细</a></div></div></section>
  </main>
  <script>
    const DETAIL_DATA = {script_json(detail_data)};
    const DEFAULT_CATEGORY = {script_json(top_categories[0][0] if top_categories else "")};
    let currentCategory = DEFAULT_CATEGORY;
    function sortValue(cell, type) {{ const raw = cell.dataset.value ?? cell.textContent.trim(); return type === "number" ? Number(raw) : raw; }}
    function sortTable(table, index, type) {{ const tbody = table.tBodies[0]; const rows = Array.from(tbody.rows); const current = table.dataset.sortIndex === String(index) ? table.dataset.sortDir : "desc"; const dir = current === "asc" ? "desc" : "asc"; rows.sort((a,b) => {{ const av = sortValue(a.cells[index], type); const bv = sortValue(b.cells[index], type); if (type === "number") return dir === "asc" ? av - bv : bv - av; return dir === "asc" ? String(av).localeCompare(String(bv), "zh-Hans-CN") : String(bv).localeCompare(String(av), "zh-Hans-CN"); }}); rows.forEach(row => tbody.appendChild(row)); table.dataset.sortIndex = String(index); table.dataset.sortDir = dir; }}
    function bindSorting(root = document) {{ root.querySelectorAll("th[data-sort]").forEach(th => {{ if (th.dataset.bound) return; th.dataset.bound = "true"; th.addEventListener("click", () => sortTable(th.closest("table"), th.cellIndex, th.dataset.sort)); }}); }}
    function renderDetails(category) {{ const data = DETAIL_DATA[category]; const tbody = document.querySelector("#detail-table tbody"); tbody.innerHTML = ""; if (!data) return; document.querySelector("#detail-title").textContent = category; document.querySelector("#detail-meta").textContent = `${{data.amount}} · ${{data.count}} 笔`; const term = document.querySelector("#detail-search").value.trim().toLowerCase(); data.rows.filter(row => !term || `${{row.date}} ${{row.merchant}}`.toLowerCase().includes(term)).forEach(row => {{ const tr = document.createElement("tr"); const dateCell = document.createElement("td"); const merchantCell = document.createElement("td"); const amountCell = document.createElement("td"); dateCell.dataset.value = row.dateValue; amountCell.dataset.value = row.amountValue; dateCell.textContent = row.date; merchantCell.textContent = row.merchant; amountCell.textContent = row.amount; tr.append(dateCell, merchantCell, amountCell); tbody.appendChild(tr); }}); bindSorting(document.querySelector("#detail-table")); }}
    document.querySelectorAll("[data-category]").forEach(button => {{ button.addEventListener("click", () => {{ currentCategory = button.dataset.category; document.querySelectorAll("[data-category]").forEach(item => item.classList.toggle("active", item === button)); document.querySelector("#detail-search").value = ""; renderDetails(currentCategory); }}); }});
    document.querySelector("#detail-search")?.addEventListener("input", () => renderDetails(currentCategory));
    bindSorting();
    const defaultButton = Array.from(document.querySelectorAll("[data-category]")).find(button => button.dataset.category === DEFAULT_CATEGORY);
    if (defaultButton) defaultButton.click();
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def analyze(input_path: Path, out_dir: Path, year: int | None, top_categories: int) -> None:
    markdown = input_path.read_text(encoding="utf-8")
    statement_year = infer_year(markdown, year)
    title = infer_title(markdown, input_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    transactions = parse_transactions(markdown, statement_year)
    matched, unmatched_refunds = match_refunds(transactions)
    cleaned = cleaned_spending(transactions)
    category_totals = aggregate_by_category(cleaned)
    daily_totals = aggregate_by_date(cleaned)

    write_pie_svg(category_totals, out_dir / "category_pie.svg")
    write_daily_svg(daily_totals, out_dir / "daily_spending.svg")
    write_csv(transactions, out_dir / "transactions_parsed.csv")
    write_csv(cleaned, out_dir / "transactions_cleaned.csv")
    write_report(
        title,
        transactions,
        cleaned,
        matched,
        unmatched_refunds,
        category_totals,
        daily_totals,
        out_dir / "report.md",
        top_categories,
    )
    write_interactive_html(
        title,
        transactions,
        cleaned,
        matched,
        unmatched_refunds,
        category_totals,
        daily_totals,
        out_dir / "report.html",
        top_categories,
    )

    cleaned_total = sum((tx.amount for tx in cleaned), Decimal("0"))
    print(f"input={input_path}")
    print(f"out={out_dir}")
    print(f"statement_year={statement_year}")
    print(f"parsed_transactions={len(transactions)}")
    print(f"cleaned_transactions={len(cleaned)}")
    print(f"matched_refunds={len(matched)}")
    print(f"unmatched_refunds={len(unmatched_refunds)}")
    print(f"cleaned_total={money(cleaned_total)}")
    print(f"report={out_dir / 'report.md'}")
    print(f"html_report={out_dir / 'report.html'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a MarkItDown-converted CMB credit card statement.")
    parser.add_argument("input", type=Path, help="Markdown file converted from a CMB credit-card statement PDF.")
    parser.add_argument("--out", type=Path, help="Output directory. Defaults to <input-stem>_analysis beside the input.")
    parser.add_argument("--year", type=int, help="Statement year, if it cannot be inferred from the Markdown.")
    parser.add_argument("--top-categories", type=int, default=3, help="Number of highest-amount categories to detail in report.md.")
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    out_dir = args.out.resolve() if args.out else input_path.with_name(f"{input_path.stem}_analysis")
    analyze(input_path, out_dir, args.year, max(0, args.top_categories))


if __name__ == "__main__":
    main()
