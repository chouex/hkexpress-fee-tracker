# -*- coding: utf-8 -*-
"""
HK Express 其他固定费用计算器（不含机票票价）
路线: HKG <-> 台北(TPE) / HKG <-> 东京(NRT)
- 自动尝试从官网抓取最新燃油附加费；抓取/解析失败则回退到内置的最新费率表。
- 燃油附加费按"每位成人、每一航段"计算 (官网规则)。
- 默认按"来回一起买"口径: 两段燃油费均以 HKD 339 计, 日本回程含日本端固定税费。
- 生成 docs/index.md (供 GitHub Pages) + hkexpress_fees.json。

用法:
    python hkexpress_fees.py            # 计算并写入产物
    python hkexpress_fees.py --check    # 仅计算并打印, 退出码 0=无变化 1=内容有变化(供 CI 判断)
"""
import argparse
import difflib
import re
import json
import datetime
import os
import urllib.request
from collections import OrderedDict

FUEL_URL = "https://www.hkexpress.com/zh-HK/Fees/Fuel-Surcharge"
DOCS_DIR = "/data/workspace/docs"
PAGE_PATH = os.path.join(DOCS_DIR, "index.md")
JSON_PATH = "/data/workspace/hkexpress_fees.json"

# 内置最新燃油费率表 (每航段/每位成人)。来源: HK Express 官网公告, 2026-08-01 起生效。
BUILTIN_FUEL = {
    "HKG": {"台湾": ("HKD", 339), "日本": ("HKD", 339), "非内地": ("HKD", 339)},
    "TPE": {"香港": ("TWD", 1370)},
    "NRT": {"香港": ("JPY", 14300)},
}
FX = {"TWD": 0.26, "JPY": 0.052}  # 参考汇率(HKD 本位), 仅显示用

HK_DEPART_TAXES = OrderedDict([
    ("香港 - 飞机乘客离境税", ("HKD", 200)),
    ("香港 - 机场建设费", ("HKD", 90)),
    ("香港 - 旅客保安费", ("HKD", 65)),
])
JP_RETURN_TAXES = OrderedDict([
    ("日本 - 国际观光旅客税", ("HKD", 149)),
    ("日本 - 乘客服务设施费", ("HKD", 147)),
])


def fetch_fuel_from_web():
    parsed = {}
    try:
        req = urllib.request.Request(FUEL_URL, headers={
            "User-Agent": "Mozilla/5.0 (HKExpressFeeBot/1.0)",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r"All Other Countries.*?HKD\s*(\d{2,4})", html, re.I | re.S)
        if m:
            v = int(m.group(1))
            parsed.setdefault("HKG", {})["非内地"] = ("HKD", v)
            parsed["HKG"]["台湾"] = ("HKD", v); parsed["HKG"]["日本"] = ("HKD", v)
        m = re.search(r"China\s*-\s*Taiwan.*?TWD\s*(\d{3,5})", html, re.I | re.S)
        if m:
            parsed.setdefault("TPE", {})["香港"] = ("TWD", int(m.group(1)))
        m = re.search(r"Japan.*?JPY\s*(\d{4,6})", html, re.I | re.S)
        if m:
            parsed.setdefault("NRT", {})["香港"] = ("JPY", int(m.group(1)))
    except Exception as e:
        parsed = {"__error__": str(e)}
    return parsed


def merge_fuel(builtin, web):
    if "__error__" in web:
        return builtin, False
    merged = {k: dict(v) for k, v in builtin.items()}
    for orig, dests in web.items():
        if orig not in merged:
            merged[orig] = {}
        if isinstance(dests, dict):
            merged[orig].update(dests)
    return merged, True


def hkd_amount(currency, amount):
    return amount if currency == "HKD" else int(round(amount * FX.get(currency, 0)))


def get_fuel(origin, dest, fuel_table, force_hkd=False):
    if force_hkd and origin in ("TPE", "NRT"):
        return ("HKD", 339)
    fuel_dests = fuel_table.get(origin, {})
    if dest in fuel_dests:
        return fuel_dests[dest]
    first_key = next(iter(fuel_dests), None)
    if first_key:
        return fuel_dests[first_key]
    return ("HKD", 339)


def calc_route(origin, dest, fuel_table, together=False):
    items = []
    hkd_total = 0
    if origin == "HKG":
        for name, (cur, amt) in HK_DEPART_TAXES.items():
            items.append((name, cur, amt, amt)); hkd_total += amt
    elif origin == "TPE":
        items.append(("中国台湾 - 机场服务费", "TWD", 125, hkd_amount("TWD", 125)))
        hkd_total += hkd_amount("TWD", 125)
    fcur, famt = get_fuel(origin, dest, fuel_table, force_hkd=together)
    items.append((f"燃油附加费 ({origin}->{dest})", fcur, famt, hkd_amount(fcur, famt)))
    hkd_total += hkd_amount(fcur, famt)
    if origin == "NRT" and together:
        for name, (cur, amt) in JP_RETURN_TAXES.items():
            items.append((name, cur, amt, amt)); hkd_total += amt
    return items, hkd_total


def build_payload(fuel_table, web_ok, together):
    routes_spec = [
        ("HKG", "台湾", "HKG -> 台北 (TPE) 去程"),
        ("TPE", "香港", "台北 (TPE) -> HKG 回程"),
        ("HKG", "日本", "HKG -> 东京 (NRT) 去程"),
        ("NRT", "香港", "东京 (NRT) -> HKG 回程"),
    ]
    payload = {"generated_at": datetime.date.today().isoformat(),
               "fuel_table": fuel_table, "web_source_used": web_ok,
               "together_booking": together, "routes": []}
    for origin, dest, label in routes_spec:
        items, subtotal = calc_route(origin, dest, fuel_table, together=together)
        payload["routes"].append({"label": label, "origin": origin, "dest": dest,
                                  "items": [{"name": n, "currency": c, "amount": a, "hkd": h}
                                            for n, c, a, h in items],
                                  "subtotal_hkd": subtotal})
    payload["round_trip_total_hkd"] = sum(r["subtotal_hkd"] for r in payload["routes"])
    return payload


def render_page(payload):
    """渲染 GitHub Pages 用的 Markdown 页面。"""
    src = "官网实时抓取" if payload["web_source_used"] else "内置最新费率表（官网抓取不可用，已回退）"
    lines = []
    lines.append("---")
    lines.append('title: "HK Express 固定费用速查"')
    lines.append(f'generated_at: "{payload["generated_at"]}"')
    lines.append("---")
    lines.append("")
    lines.append("# HK Express 其他固定费用速查（不含机票票价）")
    lines.append("")
    lines.append(f"- 计算日：**{payload['generated_at']}**")
    lines.append(f"- 燃油附加费来源：{src}")
    lines.append("- 计价模式：来回一起买（燃油费统一以 HKD 计，日本回程含日本端固定税费）")
    lines.append(f"- 路线：HKG ↔ 台北(TPE) / HKG ↔ 东京(NRT)（1 位成人／往返／不含票价）")
    lines.append("")
    lines.append("> 数据仅供参考，以 HK Express 结账页面实际列示为准。")
    lines.append("")
    lines.append("## 各航段固定费用明细")
    lines.append("")
    for r in payload["routes"]:
        lines.append(f"### {r['label']}  ")
        lines.append("")
        lines.append("| 费用项目 | 币种 | 金额 | HKD 参考 |")
        lines.append("|---|---|---:|---:|")
        for it in r["items"]:
            ref = "" if it["currency"] == "HKD" else f"{it['hkd']:,}"
            lines.append(f"| {it['name']} | {it['currency']} | {it['amount']:,} | {ref} |")
        lines.append(f"| **单程小计** | **HKD** | **{r['subtotal_hkd']:,}** | |")
        lines.append("")
    lines.append("## 往返合计固定费用（不含任何票价）")
    lines.append("")
    lines.append(f"**HKD {payload['round_trip_total_hkd']:,}**")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- 燃油附加费按官网规则“每位旅客每航段”计；2 岁以下不占座幼童免燃油费。")
    lines.append("- 香港出发固定税费：离境税 200 + 机场建设费 90 + 旅客保安费 65 = 355 HKD。")
    lines.append("- 日本回程固定税费：国际观光旅客税 149 + 乘客服务设施费 147 = 296 HKD。")
    lines.append("- 参考汇率 TWD≈0.26 / JPY≈0.052 HKD（仅显示用，非结算依据）。")
    lines.append("- 本页由 GitHub Actions 每日自动更新（如燃油附加费有变动）。")
    return "\n".join(lines) + "\n"


def render_text_report(payload):
    """人类可读的 stdout 报告（保留原有格式）。"""
    src = "官网实时抓取" if payload["web_source_used"] else "内置最新费率表(官网抓取不可用, 已回退)"
    out = []
    out.append(f"# HK Express 固定费用明细 (不含票价)  |  计算日: {payload['generated_at']}")
    out.append(f"# 燃油附加费来源: {src}\n")
    for r in payload["routes"]:
        out.append(f"## {r['label']}  (单程 / 1 位成人 / 1 航段)")
        for it in r["items"]:
            line = f"    - {it['name']}: {it['currency']} {it['amount']:,}"
            if it["currency"] != "HKD":
                line += f"  (约 HKD {it['hkd']:,})"
            out.append(line)
        out.append(f"  -> 单程固定费用小计: HKD {r['subtotal_hkd']:,}\n")
    out.append("=" * 44)
    out.append(f"往返合计固定费用 (去程+回程, 不含任何票价): HKD {payload['round_trip_total_hkd']:,}")
    out.append("=" * 44)
    return "\n".join(out)


def file_changed(path, new_content):
    """比较文件内容是否变化（文件不存在视为变化）。"""
    if not os.path.exists(path):
        return True
    with open(path, "r", encoding="utf-8") as f:
        old = f.read()
    if old == new_content:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="HK Express 固定费用计算器")
    parser.add_argument("--check", action="store_true",
                        help="仅计算并检查产物是否有变化；有变化退出码 1，否则 0（供 CI 使用）")
    args = parser.parse_args()

    web = fetch_fuel_from_web()
    fuel_table, web_ok = merge_fuel(BUILTIN_FUEL, web)
    payload = build_payload(fuel_table, web_ok, together=True)

    page_md = render_page(payload)
    json_str = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        # CI 模式: 不写入, 仅报告是否有变化
        changed = file_changed(PAGE_PATH, page_md) or file_changed(JSON_PATH, json_str)
        print(render_text_report(payload))
        print(f"\n[check] page_changed={file_changed(PAGE_PATH, page_md)} "
              f"json_changed={file_changed(JSON_PATH, json_str)}")
        return 1 if changed else 0

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(page_md)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        f.write(json_str)
    print(render_text_report(payload))
    print(f"\n已写入 {PAGE_PATH}")
    print(f"已写入 {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
