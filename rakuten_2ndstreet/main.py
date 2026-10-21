"""
楽天市場 セカンドストリート(2nd-street) 新着商品監視サーバー

楽天市場商品検索APIで 2nd-street の商品を新着順(-updateTimestamp)に
直近30件取得し、まだ通知していない商品だけを Discord Webhook に通知する。

Google Cloud Run 上で Flask サーバーとして動作させる想定。
Cloud Scheduler などから定期的に GET/POST /check を呼び出すと1回チェックが走る。

重複通知防止:
通知済みの itemCode を状態ファイル(JSON)に記録する。
Cloud Run のローカルディスクはインスタンス終了で消えてしまうため、
環境変数 STATE_BUCKET を設定した場合は Google Cloud Storage に保存する。
(未設定の場合はローカルファイル STATE_FILE に保存する)

環境変数:
  DISCORD_WEBHOOK_URL  (必須) 通知先の Discord Webhook URL
  RAKUTEN_APP_ID       楽天アプリケーションID (未設定時は既定値を使用)
  RAKUTEN_ACCESS_KEY   楽天アクセスキー (新API仕様で必須)
  RAKUTEN_REFERER      楽天アプリ設定に登録したWebサイトURL (Refererとして送信)
  RAKUTEN_API_URL      商品検索APIのエンドポイント (未設定時は既定値を使用)
  STATE_BUCKET         状態ファイルを保存する GCS バケット名 (任意)
  STATE_OBJECT         GCS上のオブジェクト名 (既定: rakuten_2ndstreet_state.json)
  STATE_FILE           ローカル保存時のファイルパス (既定: /tmp/rakuten_2ndstreet_state.json)
  PORT                 待ち受けポート (既定: 8080)
"""

import json
import logging
import os
import threading
import time

import requests
from flask import Flask, jsonify

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

RAKUTEN_API_URL = os.environ.get(
    "RAKUTEN_API_URL",
    "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701",
)
RAKUTEN_APP_ID = os.environ.get("RAKUTEN_APP_ID", "0d16e53b-cf1c-448e-aabe-5c49594bee17")
RAKUTEN_ACCESS_KEY = os.environ.get("RAKUTEN_ACCESS_KEY", "")
RAKUTEN_REFERER = os.environ.get("RAKUTEN_REFERER", "")

SHOP_CODE = "2nd-street"
SORT = "-updateTimestamp"  # 新着順
HITS = 30  # 直近30件

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

STATE_BUCKET = os.environ.get("STATE_BUCKET", "")
STATE_OBJECT = os.environ.get("STATE_OBJECT", "rakuten_2ndstreet_state.json")
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/rakuten_2ndstreet_state.json")

# 状態ファイルに残す itemCode の上限(古いものから捨てる)
MAX_STORED_CODES = 1000

# Discordは1メッセージにつき埋め込み(embed)10件まで
DISCORD_EMBEDS_PER_MESSAGE = 10

REQUEST_TIMEOUT = 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 同一インスタンス内でチェックが同時に走らないようにする
_check_lock = threading.Lock()


# ------------------------------------------------------------
# 状態(通知済み itemCode)の読み書き
# ------------------------------------------------------------

def _gcs_blob():
    from google.cloud import storage  # GCSを使うときだけ読み込む

    return storage.Client().bucket(STATE_BUCKET).blob(STATE_OBJECT)


def load_notified_codes():
    """通知済み itemCode のリストを読み込む。状態がまだ無い場合は None を返す。"""
    try:
        if STATE_BUCKET:
            blob = _gcs_blob()
            if not blob.exists():
                return None
            data = json.loads(blob.download_as_text())
        else:
            if not os.path.exists(STATE_FILE):
                return None
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("状態ファイルの読み込みに失敗したため、空として扱います: %s", e)
        return None
    return list(data.get("notified_item_codes", []))


def save_notified_codes(codes):
    codes = codes[-MAX_STORED_CODES:]
    body = json.dumps({"notified_item_codes": codes}, ensure_ascii=False, indent=2)
    if STATE_BUCKET:
        _gcs_blob().upload_from_string(body, content_type="application/json")
    else:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(body)


# ------------------------------------------------------------
# 楽天商品検索API
# ------------------------------------------------------------

def fetch_latest_items():
    """2nd-street の商品を新着順に HITS 件取得する。"""
    params = {
        "applicationId": RAKUTEN_APP_ID,
        "shopCode": SHOP_CODE,
        "sort": SORT,
        "hits": HITS,
        "format": "json",
        "formatVersion": 2,
    }
    if RAKUTEN_ACCESS_KEY:
        params["accessKey"] = RAKUTEN_ACCESS_KEY

    headers = {}
    if RAKUTEN_REFERER:
        headers["Referer"] = RAKUTEN_REFERER

    resp = requests.get(RAKUTEN_API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"楽天APIエラー: HTTP {resp.status_code} {resp.text[:300]}")

    data = resp.json()
    items = data.get("Items", [])
    # formatVersion=1 形式 ({"Item": {...}}) で返ってきた場合にも対応
    return [it.get("Item", it) for it in items]


# ------------------------------------------------------------
# Discord 通知
# ------------------------------------------------------------

def _build_embed(item):
    price = item.get("itemPrice")
    price_text = f"{price:,}円" if isinstance(price, int) else f"{price}円"
    embed = {
        "title": item.get("itemName", "(商品名なし)")[:256],
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
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("環境変数 DISCORD_WEBHOOK_URL が設定されていません")

    for i in range(0, len(items), DISCORD_EMBEDS_PER_MESSAGE):
        chunk = items[i : i + DISCORD_EMBEDS_PER_MESSAGE]
        payload = {
            "content": "🆕 セカンドストリート楽天市場店の新着商品",
            "embeds": [_build_embed(it) for it in chunk],
        }
        for _ in range(3):
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                # レート制限: 指定秒数待って再送
                wait = float(resp.json().get("retry_after", 1))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("Discordのレート制限により通知できませんでした")
        time.sleep(1)


# ------------------------------------------------------------
# チェック本体
# ------------------------------------------------------------

def run_check():
    items = fetch_latest_items()
    notified = load_notified_codes() or []
    notified_set = set(notified)

    # API は新しい順に返すので、通知は古い順に並べ替えて送る
    new_items = [it for it in items if it.get("itemCode") and it["itemCode"] not in notified_set]
    new_items.reverse()

    if new_items:
        notify_discord(new_items)
        notified.extend(it["itemCode"] for it in new_items)
        save_notified_codes(notified)

    logger.info("取得 %d件 / 新着通知 %d件", len(items), len(new_items))
    return {"fetched": len(items), "notified": len(new_items)}


# ------------------------------------------------------------
# Flask エンドポイント
# ------------------------------------------------------------

@app.route("/", methods=["GET"])
def health():
    return "ok", 200


@app.route("/check", methods=["GET", "POST"])
def check():
    if not _check_lock.acquire(blocking=False):
        return jsonify({"status": "busy"}), 409
    try:
        result = run_check()
        return jsonify({"status": "ok", **result}), 200
    except Exception as e:  # noqa: BLE001 - エラー内容をレスポンスとログに残す
        logger.exception("チェック中にエラーが発生しました")
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        _check_lock.release()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
