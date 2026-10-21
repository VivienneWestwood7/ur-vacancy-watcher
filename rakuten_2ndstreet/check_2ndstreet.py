"""
楽天市場 セカンドストリート(2nd-street) 新着商品監視スクリプト

楽天市場商品検索APIで 2nd-street の商品を新着順(-updateTimestamp)に
直近30件取得し、まだ通知していない商品だけを Discord Webhook に通知する。

GitHub Actions から定期的に1回ずつ実行する想定。
通知済みの itemCode は同じフォルダの state.json に保存し、
同じ商品を何度も通知しないようにする。

環境変数:
  DISCORD_WEBHOOK_URL  (必須) 通知先の Discord Webhook URL
  RAKUTEN_ACCESS_KEY   (必須) 楽天ウェブサービスのアクセスキー
  RAKUTEN_APP_ID       楽天アプリケーションID (未設定時は既定値を使用)
  RAKUTEN_REFERER      楽天アプリ設定に登録したWebサイトURL (Refererとして送信)
  RAKUTEN_API_URL      商品検索APIのエンドポイント (未設定時は既定値を使用)
"""

import json
import os
import sys
import time

import requests

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

RAKUTEN_API_URL = os.environ.get("RAKUTEN_API_URL") or (
    "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"
)
RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID") or "0d16e53b-cf1c-448e-aabe-5c49594bee17"
RAKUTEN_ACCESS_KEY = os.environ.get("RAKUTEN_ACCESS_KEY", "")
RAKUTEN_REFERER = os.environ.get("RAKUTEN_REFERER", "")

SHOP_CODE = "2nd-street"
SORT = "-updateTimestamp"  # 新着順
HITS = 30  # 直近30件

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# state.json に残す itemCode の上限(古いものから捨てる)
MAX_STORED_CODES = 1000

# Discordは1メッセージにつき埋め込み(embed)10件まで
DISCORD_EMBEDS_PER_MESSAGE = 10

REQUEST_TIMEOUT = 20


# ------------------------------------------------------------
# 状態(通知済み itemCode)の読み書き
# ------------------------------------------------------------

def load_notified_codes():
    if not os.path.exists(STATE_FILE):
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"state.json の読み込みに失敗したため、空として扱います: {e}")
        return []
    return list(data.get("notified_item_codes", []))


def save_notified_codes(codes):
    codes = codes[-MAX_STORED_CODES:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"notified_item_codes": codes}, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ------------------------------------------------------------
# 楽天商品検索API
# ------------------------------------------------------------

def fetch_latest_items():
    """2nd-street の商品を新着順に HITS 件取得する。"""
    params = {
        "applicationId": RAKUTEN_APP_ID,
        "accessKey": RAKUTEN_ACCESS_KEY,
        "shopCode": SHOP_CODE,
        "sort": SORT,
        "hits": HITS,
        "format": "json",
        "formatVersion": 2,
    }
    headers = {}
    if RAKUTEN_REFERER:
        headers["Referer"] = RAKUTEN_REFERER

    resp = requests.get(RAKUTEN_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"楽天APIエラー: HTTP {resp.status_code} {resp.text[:300]}")

    items = resp.json().get("Items", [])
    # formatVersion=1 形式 ({"Item": {...}}) で返ってきた場合にも対応
    return [it.get("Item", it) for it in items]


# ------------------------------------------------------------
# Discord 通知
# ------------------------------------------------------------

def build_embed(item):
    price = item.get("itemPrice")
    price_text = f"{price:,}円" if isinstance(price, int) else f"{price}円"
    embed = {
        "title": (item.get("itemName") or "(商品名なし)")[:256],
        "url": item.get("itemUrl"),
        "fields": [{"name": "価格", "value": price_text, "inline": True}],
        "color": 0xBF0000,
    }
    images = item.get("mediumImageUrls") or []
    if images:
        first = images[0]
        # formatVersion=1 では {"imageUrl": "..."} の形になる
        url = first.get("imageUrl") if isinstance(first, dict) else first
        if url:
            embed["thumbnail"] = {"url": url}
    return embed


def notify_discord(items):
    for i in range(0, len(items), DISCORD_EMBEDS_PER_MESSAGE):
        chunk = items[i : i + DISCORD_EMBEDS_PER_MESSAGE]
        payload = {
            "content": "🆕 セカンドストリート楽天市場店の新着商品",
            "embeds": [build_embed(it) for it in chunk],
        }
        for _ in range(3):
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                # レート制限: 指定秒数待って再送
                time.sleep(float(resp.json().get("retry_after", 1)))
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Discordのレート制限により通知できませんでした")
        time.sleep(1)  # Discordへの連続送信を少し間隔をあける


# ------------------------------------------------------------
# メイン
# ------------------------------------------------------------

def main():
    if not DISCORD_WEBHOOK_URL:
        print("環境変数 DISCORD_WEBHOOK_URL が設定されていません")
        sys.exit(1)
    if not RAKUTEN_ACCESS_KEY:
        print("環境変数 RAKUTEN_ACCESS_KEY が設定されていません")
        sys.exit(1)

    items = fetch_latest_items()
    notified = load_notified_codes()
    notified_set = set(notified)

    # API は新しい順に返すので、通知は古い順に並べ替えて送る
    new_items = [it for it in items if it.get("itemCode") and it["itemCode"] not in notified_set]
    new_items.reverse()

    if new_items:
        notify_discord(new_items)
        notified.extend(it["itemCode"] for it in new_items)
    if new_items or not os.path.exists(STATE_FILE):
        save_notified_codes(notified)

    print(f"取得 {len(items)}件 / 新着通知 {len(new_items)}件")


if __name__ == "__main__":
    main()
