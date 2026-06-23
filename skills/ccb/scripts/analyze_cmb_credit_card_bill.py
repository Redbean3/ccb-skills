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


def infer_title(markdown: str, input_path: Path, fallback_year: int) -> str:
    patterns = (
        r"CMB Credit Card Statement \((\d{4})\.(\d{1,2})\)",
        r"信用卡对账单.*?(\d{4})年\s*(\d{1,2})月",
        r"账单.*?(\d{4})年\s*(\d{1,2})月",
    )
    for pattern in patterns:
        match = re.search(pattern, markdown, flags=re.S)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            return f"{year}年{month:02d}月账单"

    filename_match = re.search(r"(\d{4})年\s*(\d{1,2})月|(\d{4})[._-](\d{1,2})", input_path.stem)
    if filename_match:
        year = int(filename_match.group(1) or filename_match.group(3))
        month = int(filename_match.group(2) or filename_match.group(4))
        return f"{year}年{month:02d}月账单"

    return f"{fallback_year}年账单"


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


def build_interactive_pie_svg(category_totals: dict[str, Decimal]) -> str:
    if not category_totals:
        return '<div class="empty-chart">无分类消费数据</div>'

    width, height = 920, 620
    cx, cy, radius = 295, 330, 205
    colors = [
        "#8E6F72",
        "#7A8A82",
        "#8B8FA3",
        "#B09A7F",
        "#A87972",
        "#6F7F8F",
        "#9A8B67",
        "#B7A6A0",
        "#8A7C70",
        "#C5C0B8",
    ]
    total = sum(category_totals.values(), Decimal("0"))
    start = -math.pi / 2
    svg: list[str] = [
        f'<svg class="category-chart-svg" xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="本期消费分类饼图，点击分类查看明细">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="40" y="55" font-size="28" font-family="Arial, sans-serif" font-weight="700" fill="#303235">本期消费分类饼图</text>',
        f'<text x="40" y="90" font-size="16" font-family="Arial, sans-serif" fill="#74706b">剔除已匹配退款后的消费合计：¥{money(total)}</text>',
    ]

    for index, (category, amount) in enumerate(category_totals.items()):
        fraction = float(amount / total) if total else 0
        end = start + fraction * math.tau
        pct = float(amount / total * Decimal("100")) if total else 0
        label = f"{category} {html_money(amount)} {pct:.1f}%"
        category_attr = escape(category, quote=True)
        svg.append(
            f'<path class="category-slice" data-category="{category_attr}" tabindex="0" role="button" '
            f'aria-label="{escape(label, quote=True)}" d="{pie_slice_path(cx, cy, radius, start, end)}" '
            f'fill="{colors[index % len(colors)]}" stroke="#ffffff" stroke-width="2">'
            f"<title>{escape(label)}</title></path>"
        )
        start = end

    legend_x = 560
    legend_y = 150
    for index, (category, amount) in enumerate(category_totals.items()):
        pct = float(amount / total * Decimal("100")) if total else 0
        y = legend_y + index * 46
        label = f"{category} {html_money(amount)} {pct:.1f}%"
        category_attr = escape(category, quote=True)
        color = colors[index % len(colors)]
        svg.extend(
            [
                f'<g class="category-legend" data-category="{category_attr}" tabindex="0" role="button" aria-label="{escape(label, quote=True)}">',
                f'<rect x="{legend_x}" y="{y - 16}" width="18" height="18" rx="3" fill="{color}"/>',
                f'<text x="{legend_x + 30}" y="{y}" font-size="16" font-family="Arial, sans-serif" fill="#303235">{escape(category)}</text>',
                f'<text x="{legend_x + 30}" y="{y + 21}" font-size="14" font-family="Arial, sans-serif" fill="#74706b">¥{money(amount)} · {pct:.1f}%</text>',
                "</g>",
            ]
        )
    svg.append("</svg>")
    return "\n".join(svg)


def build_interactive_daily_svg(daily_totals: dict[date, Decimal]) -> str:
    if not daily_totals:
        return '<div class="empty-chart">无每日消费数据</div>'

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

    def smooth_line_path(points: list[tuple[float, float, date, Decimal]]) -> str:
        if not points:
            return ""
        if len(points) == 1:
            return f"M {points[0][0]:.2f} {points[0][1]:.2f}"

        smoothness = 0.12

        def clamp_y(value: float) -> float:
            return min(max(value, margin_top), margin_top + chart_height)

        path = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
        for index in range(len(points) - 1):
            p0 = points[index - 1] if index > 0 else points[index]
            p1 = points[index]
            p2 = points[index + 1]
            p3 = points[index + 2] if index + 2 < len(points) else p2
            c1x = p1[0] + (p2[0] - p0[0]) * smoothness
            c1y = clamp_y(p1[1] + (p2[1] - p0[1]) * smoothness)
            c2x = p2[0] - (p3[0] - p1[0]) * smoothness
            c2y = clamp_y(p2[1] - (p3[1] - p1[1]) * smoothness)
            path.append(f"C {c1x:.2f} {c1y:.2f}, {c2x:.2f} {c2y:.2f}, {p2[0]:.2f} {p2[1]:.2f}")
        return " ".join(path)

    svg: list[str] = [
        f'<svg class="daily-chart-svg" xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="每日消费金额，点击日期查看明细">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="40" y="52" font-size="28" font-family="Arial, sans-serif" font-weight="700" fill="#303235">每日消费金额</text>',
        '<text x="40" y="80" font-size="16" font-family="Arial, sans-serif" fill="#74706b">按交易日汇总，已剔除可匹配退款对应账目</text>',
    ]

    for tick in range(6):
        amount = y_max * Decimal(tick) / Decimal(5)
        y = y_at(amount)
        svg.append(f'<line x1="{margin_left}" y1="{y:.2f}" x2="{width - margin_right}" y2="{y:.2f}" stroke="#e7e2dc" stroke-width="1"/>')
        svg.append(f'<text x="{margin_left - 12}" y="{y + 5:.2f}" font-size="12" font-family="Arial, sans-serif" text-anchor="end" fill="#74706b">{money(amount)}</text>')

    line_points: list[tuple[float, float, date, Decimal]] = []
    for index, (day, amount) in enumerate(items):
        x = x_at(index)
        y = y_at(amount)
        bar_height = margin_top + chart_height - y
        label = f"{day.strftime('%m/%d')} {html_money(amount)}"
        data_attrs = (
            f'class="daily-bar" data-day="{day.isoformat()}" tabindex="0" role="button" '
            f'aria-label="{escape(label, quote=True)}"'
            if amount
            else 'class="daily-bar empty"'
        )
        svg.append(
            f'<rect {data_attrs} x="{x - bar_width / 2:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" '
            'fill="#8E6F72" opacity="0.84">'
            f"<title>{escape(label)}</title></rect>"
        )
        line_points.append((x, y, day, amount))
        if index % 2 == 0 or index == count - 1:
            svg.append(
                f'<text x="{x:.2f}" y="{height - 52}" font-size="12" font-family="Arial, sans-serif" '
                f'text-anchor="middle" fill="#74706b" transform="rotate(-35 {x:.2f} {height - 52})">{day.strftime("%m/%d")}</text>'
            )

    if line_points:
        svg.append(
            f'<path d="{smooth_line_path(line_points)}" fill="none" stroke="#6F7F8F" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y, day, amount in line_points:
            label = f"{day.strftime('%m/%d')} {html_money(amount)}"
            data_attrs = (
                f'class="daily-dot" data-day="{day.isoformat()}" tabindex="0" role="button" '
                f'aria-label="{escape(label, quote=True)}"'
                if amount
                else 'class="daily-dot empty"'
            )
            svg.append(
                f'<circle {data_attrs} cx="{x:.2f}" cy="{y:.2f}" r="3.2" fill="#6F7F8F" stroke="#ffffff" stroke-width="1.5">'
                f"<title>{escape(label)}</title></circle>"
            )

    svg.extend(
        [
            f'<line x1="{margin_left}" y1="{margin_top + chart_height}" x2="{width - margin_right}" y2="{margin_top + chart_height}" stroke="#a9a39a"/>',
            f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + chart_height}" stroke="#a9a39a"/>',
            '<rect x="920" y="36" width="18" height="12" fill="#8E6F72" opacity="0.84"/>',
            '<text x="945" y="47" font-size="14" font-family="Arial, sans-serif" fill="#74706b">柱：日消费额</text>',
            '<line x1="920" y1="70" x2="938" y2="70" stroke="#6F7F8F" stroke-width="3"/>',
            '<text x="945" y="75" font-size="14" font-family="Arial, sans-serif" fill="#74706b">线：趋势</text>',
            "</svg>",
        ]
    )
    return "\n".join(svg)


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

    transaction_data = [
        {
            "date": tx.trans_date.strftime("%m/%d"),
            "dateValue": tx.trans_date.isoformat(),
            "merchant": tx.description,
            "category": tx.category,
            "amount": html_money(tx.amount),
            "amountValue": float(tx.amount),
        }
        for tx in sorted(cleaned, key=lambda item: (item.trans_date, item.amount), reverse=True)
    ]
    top_day_label = top_day.strftime("%m/%d") if top_day else "-"
    interactive_pie_svg = build_interactive_pie_svg(category_totals)
    interactive_daily_svg = build_interactive_daily_svg(daily_totals)

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}分析</title>
  <style>
    :root {{
      --bg:#f5f3f0;
      --surface:#ffffff;
      --surface-muted:#f8f6f3;
      --ink:#303235;
      --muted:#74706b;
      --line:#e7e2dc;
      --primary:#8e6f72;
      --primary-strong:#72595e;
      --primary-soft:#f4eeee;
      --success:#7a8a82;
      --warning:#b09a7f;
      --danger:#a87972;
      --info:#6f7f8f;
      --shadow:0 14px 34px rgba(48,50,53,.07);
      --radius:8px;
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; line-height:1.5; }}
    button, input {{ font-family:inherit; }}
    .app-shell {{ min-height:100vh; display:flex; }}
    .sidebar {{ position:sticky; top:0; height:100vh; width:256px; flex:0 0 256px; display:flex; flex-direction:column; gap:24px; padding:16px; overflow:hidden; border-right:1px solid var(--line); background:var(--surface); z-index:20; transition:width .18s ease, flex-basis .18s ease; }}
    .app-shell.sidebar-collapsed .sidebar {{ width:76px; flex-basis:76px; }}
    .brand {{ display:grid; grid-template-columns:40px minmax(0,1fr) 32px; gap:10px; align-items:center; }}
    .brand-mark {{ width:40px; height:40px; display:grid; place-items:center; padding:0; border:0; border-radius:var(--radius); background:var(--primary); color:#fff; font-size:13px; font-weight:800; letter-spacing:0; appearance:none; }}
    .brand-mark:disabled {{ opacity:1; cursor:default; }}
    .brand-title {{ margin:0; font-size:15px; font-weight:750; line-height:1.2; }}
    .brand-subtitle {{ margin:2px 0 0; color:var(--muted); font-size:12px; }}
    .sidebar-toggle {{ width:32px; height:32px; display:grid; place-items:center; border:1px solid var(--line); border-radius:var(--radius); background:#fff; color:var(--muted); cursor:pointer; font-size:20px; line-height:1; }}
    .side-nav {{ display:grid; gap:6px; }}
    .side-nav a {{ display:flex; align-items:center; gap:10px; min-height:36px; padding:8px 10px; border-radius:var(--radius); color:var(--muted); font-size:14px; text-decoration:none; }}
    .side-nav a:hover {{ background:var(--primary-soft); color:var(--primary); }}
    .nav-dot {{ width:8px; height:8px; border-radius:999px; border:2px solid currentColor; flex:0 0 auto; opacity:.7; }}
    .sidebar-note {{ margin-top:auto; padding:12px; border:1px solid var(--line); border-radius:var(--radius); background:var(--surface-muted); color:var(--muted); font-size:12px; }}
    .brand-text, .nav-label, .sidebar-note, .sidebar-toggle {{ transition:opacity .12s ease; }}
    .sidebar-expanding .brand-text, .sidebar-expanding .nav-label, .sidebar-expanding .sidebar-note, .sidebar-expanding .sidebar-toggle {{ opacity:0; pointer-events:none; }}
    .sidebar-collapsed .brand-text, .sidebar-collapsed .nav-label, .sidebar-collapsed .sidebar-note {{ display:none; }}
    .sidebar-collapsed .brand {{ grid-template-columns:40px; justify-content:center; gap:10px; }}
    .sidebar-collapsed .brand-mark {{ cursor:pointer; }}
    .sidebar-collapsed .sidebar-toggle {{ display:none; }}
    .content-shell {{ min-width:0; }}
    .topbar {{ position:sticky; top:0; z-index:12; display:flex; justify-content:space-between; gap:18px; align-items:center; padding:16px 24px; border-bottom:1px solid var(--line); background:rgba(245,243,240,.94); backdrop-filter:blur(14px); }}
    h1 {{ margin:0; font-size:24px; line-height:1.18; letter-spacing:0; }}
    .subtitle {{ margin:4px 0 0; color:var(--muted); max-width:760px; font-size:13px; }}
    .topbar-actions {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
    .button {{ display:inline-flex; align-items:center; min-height:36px; padding:8px 12px; border:1px solid var(--line); border-radius:var(--radius); color:var(--ink); background:#fff; font-size:14px; text-decoration:none; box-shadow:0 1px 2px rgba(48,50,53,.04); }}
    .button.primary {{ border-color:var(--primary); background:var(--primary); color:#fff; }}
    .dashboard-grid {{ display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:24px; padding:20px 24px 40px; }}
    .dashboard-card, .kpi-card {{ background:var(--surface); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); }}
    .kpi-row {{ grid-column:1 / -1; display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:16px; }}
    .kpi-card {{ grid-column:span 3; min-height:124px; padding:20px; }}
    .kpi-card.primary {{ border-color:#e4d6d6; background:linear-gradient(180deg,#ffffff,#fbf7f6); }}
    .kpi-card.refund {{ border-left:4px solid var(--danger); }}
    .kpi-card.net {{ border-left:4px solid var(--success); }}
    .kpi-card.day {{ border-left:4px solid var(--warning); }}
    .label {{ color:var(--muted); font-size:12px; font-weight:650; text-transform:uppercase; letter-spacing:.02em; }}
    .value {{ margin-top:10px; font-size:26px; font-weight:800; letter-spacing:0; }}
    .kpi-card.primary .value {{ color:var(--primary); font-size:30px; }}
    .sub {{ margin-top:6px; color:var(--muted); font-size:13px; }}
    .dashboard-card {{ overflow:hidden; }}
    .card-head {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; padding:20px 24px 14px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0; font-size:19px; line-height:1.25; letter-spacing:0; }}
    .note {{ margin:6px 0 0; color:var(--muted); font-size:13px; }}
    .card-body {{ padding:20px 24px 24px; }}
    .category-card {{ grid-column:span 5; }}
    .trend-card {{ grid-column:span 7; }}
    .table-card {{ grid-column:span 8; }}
    .activity-stack {{ grid-column:span 4; display:grid; gap:24px; align-self:start; }}
    .chart {{ overflow:auto; min-height:360px; }}
    .chart img, .chart svg {{ display:block; width:100%; min-width:520px; height:auto; }}
    .category-slice, .category-legend, .daily-bar, .daily-dot {{ cursor:pointer; }}
    .category-slice {{ transition:opacity .14s ease, transform .14s ease; transform-box:fill-box; transform-origin:center; }}
    .category-slice:focus, .category-legend:focus, .daily-bar:focus, .daily-dot:focus {{ outline:none; }}
    .category-slice:hover, .category-slice.active {{ opacity:.97; transform:scale(1.012); stroke:#ffffff; stroke-width:2; filter:drop-shadow(0 4px 8px rgba(142,111,114,.16)); }}
    .category-legend.active text:first-of-type {{ font-weight:700; fill:var(--primary); }}
    .category-legend:hover text:first-of-type {{ fill:var(--primary); }}
    .daily-bar:not(.empty):hover, .daily-bar.active {{ fill:var(--primary-strong); opacity:1; }}
    .daily-dot:not(.empty):hover, .daily-dot.active {{ fill:var(--primary-strong); stroke:#ffffff; }}
    .empty-state {{ color:var(--muted); padding:14px; }}
    .empty-chart {{ min-width:520px; padding:32px; color:var(--muted); }}
    .wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:var(--radius); }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th, td {{ border-bottom:1px solid var(--line); padding:12px 14px; text-align:left; vertical-align:top; }}
    tr:hover td {{ background:#faf8f5; }}
    th {{ color:#344054; background:var(--surface-muted); font-size:12px; font-weight:750; text-transform:uppercase; letter-spacing:.02em; white-space:nowrap; user-select:none; }}
    th[data-sort] {{ cursor:pointer; }} td[data-value], .num {{ text-align:right; }}
    .toolbar {{ display:flex; justify-content:space-between; gap:16px; align-items:center; margin:0 0 16px; flex-wrap:wrap; }}
    .filters {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .filter-chip, .ghost {{ border:1px solid var(--line); border-radius:var(--radius); background:#fff; color:var(--ink); font:inherit; font-size:14px; padding:8px 10px; }}
    .filter-chip.active {{ border-color:#e4d6d6; background:var(--primary-soft); color:var(--primary-strong); }}
    .filter-chip:not(.active) {{ color:var(--muted); }}
    .ghost {{ cursor:pointer; }}
    .ghost:disabled {{ opacity:.46; cursor:not-allowed; }}
    .search {{ min-width:280px; border:1px solid var(--line); border-radius:var(--radius); padding:9px 11px; font:inherit; font-size:14px; background:#fff; }}
    button:focus, a:focus, input:focus {{ outline:none; }}
    button:focus-visible, a:focus-visible, input:focus-visible {{ outline:2px solid rgba(142,111,114,.24); outline-offset:2px; }}
    .search:focus {{ border-color:#e4d6d6; box-shadow:0 0 0 3px rgba(142,111,114,.10); }}
    .detail-title {{ margin:0; font-size:18px; font-weight:750; }} .detail-meta {{ color:var(--muted); font-size:14px; }}
    details {{ border:1px solid var(--line); border-radius:var(--radius); background:var(--surface-muted); margin-top:14px; }}
    summary {{ cursor:pointer; padding:13px 15px; font-weight:700; }} .details-body {{ padding:0 15px 15px; }}
    .callout {{ border-left:4px solid var(--warning); background:#fbf6ed; padding:12px 14px; border-radius:var(--radius); color:#6f5636; margin:0 0 16px; font-size:14px; }}
    .links {{ display:flex; gap:10px; flex-wrap:wrap; }} .links a {{ color:var(--primary); border:1px solid var(--line); border-radius:var(--radius); padding:8px 10px; text-decoration:none; background:#fff; font-size:14px; }}
    @media (max-width:1180px) {{ .kpi-card {{ grid-column:span 6; }} .category-card,.trend-card,.table-card,.activity-stack {{ grid-column:1 / -1; }} }}
    @media (max-width:720px) {{ .app-shell {{ display:block; }} .sidebar {{ position:static; width:auto; flex-basis:auto; height:auto; flex-direction:row; align-items:center; overflow-x:auto; border-right:0; border-bottom:1px solid var(--line); }} .side-nav {{ display:flex; }} .brand-text,.sidebar-note,.sidebar-toggle {{ display:none; }} .content-shell {{ min-width:0; }} .topbar {{ display:block; padding:14px 16px; }} .topbar-actions {{ justify-content:flex-start; margin-top:12px; }} .dashboard-grid {{ padding:16px; gap:16px; }} .kpi-row {{ gap:12px; }} .kpi-card {{ grid-column:1 / -1; }} h1 {{ font-size:22px; }} .card-head,.card-body {{ padding-left:16px; padding-right:16px; }} .value,.kpi-card.primary .value {{ font-size:24px; }} table {{ font-size:13px; }} .search {{ min-width:100%; }} }}
  </style>
</head>
<body>
  <div class="app-shell" id="app-shell">
    <aside class="sidebar" aria-label="报告导航">
      <div class="brand">
        <button type="button" class="brand-mark" id="brand-mark" aria-label="账单分析标识" disabled>CCB</button>
        <div class="brand-text"><p class="brand-title">账单分析</p><p class="brand-subtitle">本地离线仪表盘</p></div>
        <button type="button" class="sidebar-toggle" id="sidebar-toggle" aria-label="折叠侧边栏">&lsaquo;</button>
      </div>
      <nav class="side-nav">
        <a href="#overview"><span class="nav-dot"></span><span class="nav-label">关键指标</span></a>
        <a href="#category-chart"><span class="nav-dot"></span><span class="nav-label">分类结构</span></a>
        <a href="#daily-chart"><span class="nav-dot"></span><span class="nav-label">每日趋势</span></a>
        <a href="#details"><span class="nav-dot"></span><span class="nav-label">消费明细</span></a>
        <a href="#refunds"><span class="nav-dot"></span><span class="nav-label">退款处理</span></a>
        <a href="#rules"><span class="nav-dot"></span><span class="nav-label">分析口径</span></a>
      </nav>
      <div class="sidebar-note">报告只引用本地生成文件，不加载外部资源。</div>
    </aside>
    <div class="content-shell">
      <header class="topbar">
        <div><h1>{escape(title)}分析</h1><p class="subtitle">基于 MarkItDown 转换后的账单明细生成；已剔除本期可匹配退款对应账目，保留未匹配退款作为账单调整。</p></div>
        <div class="topbar-actions"><a class="button primary" href="transactions_cleaned.csv">清洗后明细</a><a class="button" href="transactions_parsed.csv">完整解析</a></div>
      </header>
      <main class="dashboard-grid">
        <section class="kpi-row" id="overview" aria-label="关键指标">
          <div class="kpi-card primary"><div class="label">分类分析消费合计</div><div class="value">{html_money(cleaned_total)}</div><div class="sub">{len(cleaned)} 笔有效消费</div></div>
          <div class="kpi-card refund"><div class="label">已匹配退款剔除</div><div class="value">{html_money(matched_refund_total)}</div><div class="sub">{len(matched)} 笔对应账目</div></div>
          <div class="kpi-card net"><div class="label">正向入账消费/分期</div><div class="value">{html_money(gross_positive)}</div><div class="sub">退款剔除前总额</div></div>
          <div class="kpi-card day"><div class="label">单日消费最高</div><div class="value">{top_day_label}</div><div class="sub">{html_money(top_day_amount)}</div></div>
        </section>
        <section class="dashboard-card category-card" id="category-chart"><div class="card-head"><div><h2>分类结构</h2><p class="note">按剔除退款后的有效消费汇总。</p></div></div><div class="card-body"><div class="chart">{interactive_pie_svg}</div></div></section>
        <section class="dashboard-card trend-card" id="daily-chart"><div class="card-head"><div><h2>每日趋势</h2><p class="note">按交易日汇总消费金额。</p></div></div><div class="card-body"><div class="chart">{interactive_daily_svg}</div></div></section>
        <section class="dashboard-card table-card" id="details"><div class="card-head"><div><h2>消费明细</h2><p class="note">图表、筛选和搜索共同作用于同一张表。</p></div></div><div class="card-body"><div class="toolbar"><div><p class="detail-title" id="detail-title">全部消费明细</p><div class="detail-meta" id="detail-meta"></div></div><input class="search" id="detail-search" type="search" placeholder="搜索日期、分类或商户"></div><div class="toolbar"><div class="filters"><button type="button" class="filter-chip" id="category-filter">分类：全部</button><button type="button" class="filter-chip" id="day-filter">日期：全部</button></div><button type="button" class="ghost" id="clear-filters" disabled>清除筛选</button></div><div class="wrap"><table class="sortable" id="detail-table"><thead><tr><th data-sort="text">交易日</th><th data-sort="text">分类</th><th data-sort="text">商户</th><th class="num" data-sort="number">金额</th></tr></thead><tbody></tbody></table><div class="empty-state" id="detail-empty" hidden>暂无匹配消费明细。</div></div></div></section>
        <aside class="activity-stack">
          <section class="dashboard-card" id="refunds"><div class="card-head"><div><h2>退款处理</h2><p class="note">匹配退款从分类与图表中剔除。</p></div></div><div class="card-body"><p class="callout">若再扣除未匹配退款 {html_money(unmatched_refund_total)}，本期净账单口径金额为 {html_money(net_after_unmatched_refunds)}。</p><details open><summary>已剔除的退款对应账目（{len(matched)} 笔）</summary><div class="details-body wrap"><table class="sortable"><thead><tr><th data-sort="text">退款交易日</th><th class="num" data-sort="number">退款金额</th><th data-sort="text">被剔除交易日</th><th data-sort="text">被剔除商户</th></tr></thead><tbody>{"".join(matched_rows)}</tbody></table></div></details><details><summary>未匹配到本期正向交易的退款（{len(unmatched_refunds)} 笔）</summary><div class="details-body wrap"><table class="sortable"><thead><tr><th data-sort="text">退款交易日</th><th data-sort="text">摘要</th><th class="num" data-sort="number">金额</th></tr></thead><tbody>{"".join(unmatched_rows) if unmatched_rows else '<tr><td colspan="3">无</td></tr>'}</tbody></table></div></details></div></section>
          <section class="dashboard-card" id="rules"><div class="card-head"><div><h2>分析口径</h2></div></div><div class="card-body"><ul><li>还款记录不计入消费分析。</li><li>正向消费与同金额、交易日不晚于退款日的退款成对剔除。</li><li>未匹配退款不归入任何消费分类，仅作为账单调整。</li><li>出行交通包含滴滴顺风车、滴滴出行、高德打车、交通、一卡通等。</li><li>食堂单独成类，其他餐饮、食品、商超归入其他饮食/食品商超。</li></ul><div class="links"><a href="transactions_cleaned.csv">清洗后消费明细</a><a href="transactions_parsed.csv">完整解析明细</a></div></div></section>
        </aside>
      </main>
    </div>
  </div>
  <script>
    const TRANSACTIONS = {script_json(transaction_data)};
    const state = {{ category: "", day: "", search: "" }};
    function sortValue(cell, type) {{ const raw = cell.dataset.value ?? cell.textContent.trim(); return type === "number" ? Number(raw) : raw; }}
    function sortTable(table, index, type) {{ const tbody = table.tBodies[0]; const rows = Array.from(tbody.rows); const current = table.dataset.sortIndex === String(index) ? table.dataset.sortDir : "desc"; const dir = current === "asc" ? "desc" : "asc"; rows.sort((a,b) => {{ const av = sortValue(a.cells[index], type); const bv = sortValue(b.cells[index], type); if (type === "number") return dir === "asc" ? av - bv : bv - av; return dir === "asc" ? String(av).localeCompare(String(bv), "zh-Hans-CN") : String(bv).localeCompare(String(av), "zh-Hans-CN"); }}); rows.forEach(row => tbody.appendChild(row)); table.dataset.sortIndex = String(index); table.dataset.sortDir = dir; }}
    function bindSorting(root = document) {{ root.querySelectorAll("th[data-sort]").forEach(th => {{ if (th.dataset.bound) return; th.dataset.bound = "true"; th.addEventListener("click", () => sortTable(th.closest("table"), th.cellIndex, th.dataset.sort)); }}); }}
    function formatMoney(value) {{ return `¥${{value.toLocaleString("zh-CN", {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})}}`; }}
    function filteredRows() {{ const term = state.search.trim().toLowerCase(); return TRANSACTIONS.filter(row => {{ const haystack = `${{row.date}} ${{row.category}} ${{row.merchant}}`.toLowerCase(); return (!state.category || row.category === state.category) && (!state.day || row.dateValue === state.day) && (!term || haystack.includes(term)); }}); }}
    function syncActive() {{ document.querySelectorAll("[data-category]").forEach(item => item.classList.toggle("active", Boolean(state.category) && item.dataset.category === state.category)); document.querySelectorAll("[data-day]").forEach(item => item.classList.toggle("active", Boolean(state.day) && item.dataset.day === state.day)); const categoryChip = document.querySelector("#category-filter"); const dayChip = document.querySelector("#day-filter"); categoryChip.textContent = state.category ? `分类：${{state.category}}` : "分类：全部"; dayChip.textContent = state.day ? `日期：${{state.day.slice(5).replace("-", "/")}}` : "日期：全部"; categoryChip.classList.toggle("active", Boolean(state.category)); dayChip.classList.toggle("active", Boolean(state.day)); document.querySelector("#clear-filters").disabled = !(state.category || state.day || state.search); }}
    function renderDetails() {{ const rows = filteredRows(); const tbody = document.querySelector("#detail-table tbody"); const empty = document.querySelector("#detail-empty"); tbody.innerHTML = ""; rows.forEach(row => {{ const tr = document.createElement("tr"); const dateCell = document.createElement("td"); const categoryCell = document.createElement("td"); const merchantCell = document.createElement("td"); const amountCell = document.createElement("td"); dateCell.dataset.value = row.dateValue; amountCell.dataset.value = row.amountValue; dateCell.textContent = row.date; categoryCell.textContent = row.category; merchantCell.textContent = row.merchant; amountCell.textContent = row.amount; tr.append(dateCell, categoryCell, merchantCell, amountCell); tbody.appendChild(tr); }}); const amount = rows.reduce((sum, row) => sum + Number(row.amountValue), 0); const title = state.category || state.day ? "筛选消费明细" : "全部消费明细"; document.querySelector("#detail-title").textContent = title; document.querySelector("#detail-meta").textContent = `${{rows.length}} / ${{TRANSACTIONS.length}} 笔 · ${{formatMoney(amount)}}`; empty.hidden = rows.length > 0; }}
    function renderDashboard() {{ syncActive(); renderDetails(); }}
    function selectCategory(category) {{ state.category = state.category === category ? "" : category; renderDashboard(); }}
    function selectDay(day) {{ state.day = state.day === day ? "" : day; renderDashboard(); }}
    function bindSelection(selector, attr, handler) {{ document.querySelectorAll(selector).forEach(item => {{ if (item.classList.contains("empty")) return; item.addEventListener("click", () => handler(item.dataset[attr])); item.addEventListener("keydown", event => {{ if (event.key === "Enter" || event.key === " ") {{ event.preventDefault(); handler(item.dataset[attr]); }} }}); }}); }}
    bindSelection("[data-category]", "category", selectCategory);
    bindSelection("[data-day]", "day", selectDay);
    document.querySelector("#detail-search")?.addEventListener("input", event => {{ state.search = event.target.value; renderDashboard(); }});
    document.querySelector("#category-filter")?.addEventListener("click", () => {{ state.category = ""; renderDashboard(); }});
    document.querySelector("#day-filter")?.addEventListener("click", () => {{ state.day = ""; renderDashboard(); }});
    document.querySelector("#clear-filters")?.addEventListener("click", () => {{ state.category = ""; state.day = ""; state.search = ""; document.querySelector("#detail-search").value = ""; renderDashboard(); }});
    const appShell = document.querySelector("#app-shell");
    const sidebarToggle = document.querySelector("#sidebar-toggle");
    const brandMark = document.querySelector("#brand-mark");
    function syncSidebarControls() {{ if (!appShell || !sidebarToggle || !brandMark) return; const collapsed = appShell.classList.contains("sidebar-collapsed"); sidebarToggle.setAttribute("aria-label", "折叠侧边栏"); brandMark.disabled = !collapsed; brandMark.setAttribute("aria-label", collapsed ? "展开侧边栏" : "账单分析标识"); }}
    function collapseSidebar() {{ appShell?.classList.remove("sidebar-expanding"); appShell?.classList.add("sidebar-collapsed"); syncSidebarControls(); }}
    function expandSidebar() {{ if (!appShell) return; appShell.classList.add("sidebar-expanding"); appShell.classList.remove("sidebar-collapsed"); syncSidebarControls(); window.setTimeout(() => appShell.classList.remove("sidebar-expanding"), 190); }}
    sidebarToggle?.addEventListener("click", collapseSidebar);
    brandMark?.addEventListener("click", expandSidebar);
    syncSidebarControls();
    bindSorting();
    renderDashboard();
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def analyze(input_path: Path, out_dir: Path, year: int | None) -> None:
    markdown = input_path.read_text(encoding="utf-8")
    statement_year = infer_year(markdown, year)
    title = infer_title(markdown, input_path, statement_year)
    out_dir.mkdir(parents=True, exist_ok=True)

    transactions = parse_transactions(markdown, statement_year)
    matched, unmatched_refunds = match_refunds(transactions)
    cleaned = cleaned_spending(transactions)
    category_totals = aggregate_by_category(cleaned)
    daily_totals = aggregate_by_date(cleaned)

    for stale_name in ("report.md", "category_pie.svg", "daily_spending.svg"):
        stale_path = out_dir / stale_name
        if stale_path.exists():
            stale_path.unlink()

    write_csv(transactions, out_dir / "transactions_parsed.csv")
    write_csv(cleaned, out_dir / "transactions_cleaned.csv")
    write_interactive_html(
        title,
        transactions,
        cleaned,
        matched,
        unmatched_refunds,
        category_totals,
        daily_totals,
        out_dir / "report.html",
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
    print(f"html_report={out_dir / 'report.html'}")
    print(f"cleaned_csv={out_dir / 'transactions_cleaned.csv'}")
    print(f"parsed_csv={out_dir / 'transactions_parsed.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a MarkItDown-converted CMB credit card statement.")
    parser.add_argument("input", type=Path, help="Markdown file converted from a CMB credit-card statement PDF.")
    parser.add_argument("--out", type=Path, help="Output directory. Defaults to <input-stem>_analysis beside the input.")
    parser.add_argument("--year", type=int, help="Statement year, if it cannot be inferred from the Markdown.")
    args = parser.parse_args()

    input_path = args.input.resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    out_dir = args.out.resolve() if args.out else input_path.with_name(f"{input_path.stem}_analysis")
    analyze(input_path, out_dir, args.year)


if __name__ == "__main__":
    main()
