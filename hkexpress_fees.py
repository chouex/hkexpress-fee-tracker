# -*- coding: utf-8 -*-
"""
HK Express 其他固定費用計算器（不含機票票價）
路線: HKG <-> 台北(TPE) / HKG <-> 東京(NRT)
- 自動嘗試從官網抓取最新燃油附加費；抓取/解析失敗則回退到內置的最新費率表。
- 燃油附加費按「每位成人、每一航段」計算（官網規則）。
- 預設按「來回一起買」口徑：兩段燃油費均以 HKD 339 計，日本回程含日本端固定費用。
- 產生 docs/index.md（供 GitHub Pages）+ hkexpress_fees.json。

用法:
    python hkexpress_fees.py            # 計算並寫入產物
    python hkexpress_fees.py --check    # 僅計算並列印，退出碼 0=無變化 1=內容有變化（供 CI 判斷）
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
    ("香港 - 飛機乘客離境稅", ("HKD", 200)),
    ("香港 - 機場建設費", ("HKD", 90)),
    ("香港 - 旅客保安費", ("HKD", 65)),
])
JP_RETURN_TAXES = OrderedDict([
    ("日本 - 國際觀光旅客稅", ("HKD", 149)),
    ("日本 - 乘客服務設施費", ("HKD", 147)),
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
        items.append(("中國台灣 - 機場服務費", "TWD", 125, hkd_amount("TWD", 125)))
        hkd_total += hkd_amount("TWD", 125)
    fcur, famt = get_fuel(origin, dest, fuel_table, force_hkd=together)
    items.append((f"燃油附加費 ({origin}->{dest})", fcur, famt, hkd_amount(fcur, famt)))
    hkd_total += hkd_amount(fcur, famt)
    if origin == "NRT" and together:
        for name, (cur, amt) in JP_RETURN_TAXES.items():
            items.append((name, cur, amt, amt)); hkd_total += amt
    return items, hkd_total


def build_payload(fuel_table, web_ok, together):
    routes_spec = [
        ("HKG", "台灣", "HKG -> 台北 (TPE) 去程"),
        ("TPE", "香港", "台北 (TPE) -> HKG 回程"),
        ("HKG", "日本", "HKG -> 東京 (NRT) 去程"),
        ("NRT", "香港", "東京 (NRT) -> HKG 回程"),
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
    src = "官網即時抓取" if payload["web_source_used"] else "內置最新費率表（官網抓取不可用，已回退）"
    lines = []
    lines.append("---")
    lines.append('title: "HK Express 固定費用速查"')
    lines.append(f'generated_at: "{payload["generated_at"]}"')
    lines.append("---")
    lines.append("")
    lines.append("# HK Express 其他固定費用速查（不含機票票價）")
    lines.append("")
    lines.append(f"- 計算日：**{payload['generated_at']}**")
    lines.append(f"- 燃油附加費來源：{src}")
    lines.append("- 計價模式：來回一起買（燃油費統一以 HKD 計，日本回程含日本端固定費用）")
    lines.append(f"- 路線：HKG ↔ 台北(TPE) / HKG ↔ 東京(NRT)（1 位成人／往返／不含票價）")
    lines.append("")
    lines.append("> 資料僅供參考，以 HK Express 結賬頁面實際列示為準。")
    lines.append("")
    lines.append("## 各航段固定費用明細")
    lines.append("")
    for r in payload["routes"]:
        lines.append(f"### {r['label']}  ")
        lines.append("")
        lines.append("| 費用項目 | 幣種 | 金額 | HKD 參考 |")
        lines.append("|---|---|---:|---:|")
        for it in r["items"]:
            ref = "" if it["currency"] == "HKD" else f"{it['hkd']:,}"
            lines.append(f"| {it['name']} | {it['currency']} | {it['amount']:,} | {ref} |")
        lines.append(f"| **單程小計** | **HKD** | **{r['subtotal_hkd']:,}** | |")
        lines.append("")
    lines.append("## 往返合計固定費用（不含任何票價）")
    lines.append("")
    lines.append(f"**HKD {payload['round_trip_total_hkd']:,}**")
    lines.append("")
    lines.append("## 說明")
    lines.append("")
    lines.append("- 燃油附加費按官網規則「每位旅客每航段」計；2 歲以下不佔座幼童免燃油費。")
    lines.append("- 香港出發固定費用：離境稅 200 + 機場建設費 90 + 旅客保安費 65 = 355 HKD。")
    lines.append("- 日本回程固定費用：國際觀光旅客稅 149 + 乘客服務設施費 147 = 296 HKD。")
    lines.append("- 參考匯率 TWD≈0.26／JPY≈0.052 HKD（僅顯示用，非結算依據）。")
    lines.append("- 本頁由 GitHub Actions 每日自動更新（如燃油附加費有變動）。")
    return "\n".join(lines) + "\n"


def render_text_report(payload):
    """人类可读的 stdout 报告（保留原有格式）。"""
    src = "官網即時抓取" if payload["web_source_used"] else "內置最新費率表(官網抓取不可用, 已回退)"
    out = []
    out.append(f"# HK Express 固定費用明細 (不含票價)  |  計算日: {payload['generated_at']}")
    out.append(f"# 燃油附加費來源: {src}\n")
    for r in payload["routes"]:
        out.append(f"## {r['label']}  (單程 / 1 位成人 / 1 航段)")
        for it in r["items"]:
            line = f"    - {it['name']}: {it['currency']} {it['amount']:,}"
            if it["currency"] != "HKD":
                line += f"  (約 HKD {it['hkd']:,})"
            out.append(line)
        out.append(f"  -> 單程固定費用小計: HKD {r['subtotal_hkd']:,}\n")
    out.append("=" * 44)
    out.append(f"往返合計固定費用 (去程+回程, 不含任何票價): HKD {payload['round_trip_total_hkd']:,}")
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
        # CI 模式: 不寫入，僅報告是否有變化
        page_changed = file_changed(PAGE_PATH, page_md)
        json_changed = file_changed(JSON_PATH, json_str)
        changed = page_changed or json_changed
        print(render_text_report(payload))
        print(f"\n[check] page_changed={page_changed} json_changed={json_changed}")
        # 輸出供 GitHub Actions 條件判斷使用
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as gh:
                gh.write(f"changed={'true' if changed else 'false'}\n")
        if changed:
            print("[check] 偵測到變動，需更新 (exit 0)")
            return 0
        print("[check] 無變動，跳過更新 (exit 1)")
        return 1

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
