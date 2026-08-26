#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HK Express 固定費用計算器（不含票價）
- 航段：HKG↔台北 / HKG↔東京
- 自動抓取 HK Express 官網燃油附加費；抓取失敗時回退至內置最新費率表
- 支援 --check 模式：僅比對產物是否有變動（供 CI 使用）
- 產出 GitHub Pages 頁面 (docs/index.md) 與結構化資料 (hkexpress_fees.json)
"""

import os
import sys
import json
import re
import urllib.request
import urllib.error
from datetime import datetime

# ── 路徑設定（使用倉庫相對路徑，避免 GitHub Actions runner 權限問題）──────
HERE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(HERE, "docs")
PAGE_PATH = os.path.join(DOCS_DIR, "index.md")
JSON_PATH = os.path.join(HERE, "hkexpress_fees.json")

FUEL_URL = "https://www.hkexpress.com/zh-HK/Fees/Fuel-Surcharge"

# 內置回退費率表（2026-08-01 起生效，官方公告標準）
# 單位：HKD / 航段（香港出發→非內地）
FUEL_HKD = 339
# 台灣出發→香港：TWD 1,370 / 航段
FUEL_TWD = 1370
# 日本出發→香港：JPY 14,300 / 航段
FUEL_JPY = 14300

# 參考匯率（僅供頁面展示，非結算依據）
RATE_TWD = 0.2588   # TWD -> HKD
RATE_JPY = 0.0517   # JPY -> HKD

TODAY = datetime.now().strftime("%Y-%m-%d")


def fetch_fuel_from_web():
    """嘗試從官網抓取燃油附加費，成功回傳 dict，失敗回傳 None"""
    try:
        req = urllib.request.Request(FUEL_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; HKExpressFeeTracker/1.0)"
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # 解析模式：非內地 / China-Taiwan / Japan 對應金額（HKD/TWD/JPY）
        data = {}
        m = re.search(r"All\s*Other\s*Countries[^0-9]*HKD\s*([0-9]+)", html, re.I | re.S)
        if m:
            data["hkd"] = int(m.group(1))
        m = re.search(r"China[^0-9]*Taiwan[^0-9]*HKD\s*([0-9]+)", html, re.I | re.S)
        if m:
            data["hkd_tw"] = int(m.group(1))
        m = re.search(r"Japan[^0-9]*HKD\s*([0-9]+)", html, re.I | re.S)
        if m:
            data["hkd_jp"] = int(m.group(1))
        if data:
            return data
    except (urllib.error.URLError, TimeoutError, ValueError):
        pass
    return None


def build_segments(fuel):
    """建立四個航段的固定費用明細（不含票價）"""
    hkd = fuel.get("hkd", FUEL_HKD)
    # 來回一起買口徑：兩段燃油費統一以 HKD 計
    fuel_hkd = hkd

    segments = [
        {
            "route": "HKG→台北",
            "dest": "台灣",
            "items": [
                ("飛機乘客離境稅", 200),
                ("機場建設費", 90),
                ("旅客保安費", 65),
                ("燃油附加費", fuel_hkd),
            ],
        },
        {
            "route": "台北→HKG",
            "dest": "台灣",
            "items": [
                ("機場服務費 (TWD 125)", round(125 * RATE_TWD)),
                ("燃油附加費", fuel_hkd),
            ],
        },
        {
            "route": "HKG→東京",
            "dest": "日本",
            "items": [
                ("飛機乘客離境稅", 200),
                ("機場建設費", 90),
                ("旅客保安費", 65),
                ("燃油附加費", fuel_hkd),
            ],
        },
        {
            "route": "東京→HKG",
            "dest": "日本",
            "items": [
                ("燃油附加費", fuel_hkd),
                ("國際觀光旅客稅", 149),
                ("乘客服務設施費", 147),
            ],
        },
    ]
    for seg in segments:
        seg["subtotal"] = sum(v for _, v in seg["items"])
    return segments


def render_page(segments, fuel):
    """渲染 GitHub Pages 頁面（繁體中文）"""
    lines = []
    lines.append("---")
    lines.append("layout: page")
    lines.append("title: HK Express 固定費用計算")
    lines.append("---")
    lines.append("")
    lines.append("# HK Express 固定費用計算（不含票價）")
    lines.append("")
    lines.append("最後更新：**%s**" % TODAY)
    lines.append("")
    lines.append("> 燃油附加費來源：HK Express 官網（`%s`）" % FUEL_URL)
    lines.append("> 現行標準（2026-08-01 起生效）：香港出發→非內地 HKD %d / 航段" % fuel.get("hkd", FUEL_HKD))
    lines.append("> 參考匯率：TWD 1 ≈ HKD %.4f；JPY 1 ≈ HKD %.4f（僅供展示）" % (RATE_TWD, RATE_JPY))
    lines.append("")
    lines.append("## 路線費用明細（1 位成人 / 來回）")
    lines.append("")
    lines.append("| 航段 | 費用項目 | 金額 (HKD) |")
    lines.append("|---|---|---:|")
    for seg in segments:
        detail = " + ".join("%s %d" % (n, v) for n, v in seg["items"])
        lines.append("| **%s** | %s | **%d** |" % (seg["route"], detail, seg["subtotal"]))
    lines.append("")

    # 按目的地分別計算來回合計
    tw_go = next(s for s in segments if s["route"] == "HKG→台北")
    tw_back = next(s for s in segments if s["route"] == "台北→HKG")
    jp_go = next(s for s in segments if s["route"] == "HKG→東京")
    jp_back = next(s for s in segments if s["route"] == "東京→HKG")
    tw_total = tw_go["subtotal"] + tw_back["subtotal"]
    jp_total = jp_go["subtotal"] + jp_back["subtotal"]

    lines.append("## 來回合計固定費用（去程＋回程，不含任何票價）")
    lines.append("")
    lines.append("| 目的地 | 去程 (HKD) | 回程 (HKD) | 來回合計 (HKD) |")
    lines.append("|---|---:|---:|---:|")
    lines.append("| 🇹🇼 台灣 | %d | %d | **%d** |" % (tw_go["subtotal"], tw_back["subtotal"], tw_total))
    lines.append("| 🇯🇵 日本 | %d | %d | **%d** |" % (jp_go["subtotal"], jp_back["subtotal"], jp_total))
    lines.append("")
    lines.append("> **台灣來回合計：HKD %d**　|　**🇯🇵 日本來回合計：HKD %d**" % (tw_total, jp_total))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本頁由 GitHub Actions 每日自動更新。燃油附加費如有變動，將自動提交新版。*")
    return "\n".join(lines) + "\n"


def main():
    check_mode = "--check" in sys.argv

    # 嘗試官網抓取；失敗則回退
    web = fetch_fuel_from_web()
    fuel = {"hkd": FUEL_HKD, "web_used": False}
    if web and "hkd" in web:
        fuel["hkd"] = web["hkd"]
        fuel["web_used"] = True

    segments = build_segments(fuel)
    page_md = render_page(segments, fuel)

    json_payload = {
        "updated_at": TODAY,
        "fuel_surcharge": {
            "hkd_per_segment": fuel["hkd"],
            "source": FUEL_URL,
            "web_used": fuel["web_used"],
        },
        "segments": [
            {
                "route": s["route"],
                "destination": s["dest"],
                "items": [{"name": n, "amount_hkd": v} for n, v in s["items"]],
                "subtotal_hkd": s["subtotal"],
            } for s in segments
        ],
        "round_trip_totals": {
            "台灣": sum(s["subtotal"] for s in segments if s["dest"] == "台灣"),
            "日本": sum(s["subtotal"] for s in segments if s["dest"] == "日本"),
        },
    }

    # 比對既有產物
    page_changed = True
    json_changed = True
    if os.path.exists(PAGE_PATH):
        with open(PAGE_PATH, encoding="utf-8") as f:
            page_changed = f.read() != page_md
    if os.path.exists(JSON_PATH):
        try:
            with open(JSON_PATH, encoding="utf-8") as f:
                json_changed = json.load(f) != json_payload
        except (json.JSONDecodeError, ValueError):
            json_changed = True

    if check_mode:
        print("[check] page_changed=%s json_changed=%s" % (page_changed, json_changed))
        if page_changed or json_changed:
            print("[check] 檢測到變動，需要更新 (exit 0)")
            return 0
        print("[check] 無變動，跳過更新 (exit 1)")
        return 1

    # 一般模式：寫入產物
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(page_md)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2)

    print("✅ 固定費用頁面已產生：%s" % PAGE_PATH)
    print("✅ 結構化資料已產生：%s" % JSON_PATH)
    print("")
    print("來回合計：台灣 HKD %d　|　🇯🇵 日本 HKD %d" % (
        json_payload["round_trip_totals"]["台灣"],
        json_payload["round_trip_totals"]["日本"],
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
