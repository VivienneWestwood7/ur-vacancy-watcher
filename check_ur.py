"""
UR賃貸住宅 空室監視スクリプト

指定した団地ページを1回ずつチェックし、
・床面積が MIN_AREA_SQM 以上の空室が新しく出ていたら
・Discord Webhook に通知する

前回チェック時に見つかった空室IDは state.json に保存し、
同じ部屋を何度も通知しないようにする。
(いったん空室が消えて、後日また同じ部屋番号で空室になった場合は
 「新しい空室」として再通知する仕様)

補足:
UR賃貸のページは、空室一覧部分がJavaScriptで後から読み込まれる作りに
なっているため、ページのHTMLを取得するだけでは空室情報を取得できない。
そのため本スクリプトでは、ページが内部で呼び出しているAPI
(https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/)
を直接呼び出して空室データ(JSON)を取得している。
"""

import html
import json
import os
import random
import re
import sys
import time
import urllib.parse

import requests

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

# 監視したいUR団地ページ一覧 (物件名, URL)
# 物件名を追加・削除・変更したい場合はこのリストを編集してください。
TARGET_PROPERTIES = [
    ("シティハイツ南大沢", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_3770.html"),
    ("南大沢学園二番街", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_5070.html"),
    ("ライブ長池 蓮生寺公園通り二番街", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_5080.html"),
    ("ベルコリーヌ南大沢", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_4340.html"),
    ("ライブ長池 コリナス長池", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_4490.html"),
    ("ライブ長池 長池公園せせらぎ通り北", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_5060.html"),
    ("ライブ長池 長池公園せせらぎ通り南", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_5100.html"),
    ("南大沢学園四番街", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_5280.html"),
    ("グランピア南大沢", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_6250.html"),
    ("ライブ長池 ビューコート別所", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_6260.html"),
    ("光が丘パークタウン 大通り中央", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_4550.html"),
    ("光が丘パークタウン 大通り南", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_3690.html"),
    ("光が丘パークタウン プロムナード十番街", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_4350.html"),
    ("シャレール荻窪", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_7130.html"),
    ("アーバンライフゆりの木通り東", "https://www.ur-net.go.jp/chintai/kanto/tokyo/20_4590.html"),
]

# 通知する床面積の下限(㎡)
MIN_AREA_SQM = 80.0

# UR内部APIのエンドポイント(ページ内のJavaScriptが呼び出しているものと同じ)
ROOM_API_URL = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/"

# 状態保存ファイル(前回チェック時に見つかった空室IDを記録する)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# Discord Webhook URL は環境変数から読み込む(GitHub Secretsに保存したもの)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

BASE_ORIGIN = "https://www.ur-net.go.jp"

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.8",
}

REQUEST_TIMEOUT = 20  # 秒
MAX_PAGES_PER_PROPERTY = 20  # 空室APIのページネーション上限(暴走防止)

# 団地ページURLから shisya(支社)/danchi(団地)/shikibetu(識別) コードを取り出す
URL_CODE_PATTERN = re.compile(r"/(\d+)_(\d{3})(\d)\.html")


# ------------------------------------------------------------
# UR空室情報の取得
# ------------------------------------------------------------

def parse_area_sqm(floorspace_raw: str):
    """'97&#13217;' のようなテキストから面積(float)を取り出す。失敗時はNone"""
    if not floorspace_raw:
        return None
    text = html.unescape(floorspace_raw)
    match = re.search(r"([\d.]+)\s*㎡", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def fetch_rooms_via_api(page_url: str, property_name: str):
    """UR内部APIを叩いて、対象団地の空室一覧を取得する"""
    m = URL_CODE_PATTERN.search(page_url)
    if not m:
        raise ValueError(f"URLから団地コードを取り出せませんでした: {page_url}")
    shisya, danchi, shikibetu = m.group(1), m.group(2), m.group(3)

    api_headers = dict(COMMON_HEADERS)
    api_headers.update(
        {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_ORIGIN,
            "Referer": page_url,
        }
    )

    rooms = []
    for page_index in range(MAX_PAGES_PER_PROPERTY):
        payload = {
            "shisya": shisya,
            "danchi": danchi,
            "shikibetu": shikibetu,
            "orderByField": "0",
            "orderBySort": "0",
            "pageIndex": str(page_index),
            "sp": "",
        }
        resp = requests.post(
            ROOM_API_URL, headers=api_headers, data=payload, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

        if not data:  # 空室が無い/ページが無い場合、APIは null を返す
            break

        floor_all = data[0].get("floorAll") or ""

        for item in data:
            room_id_raw = item.get("id") or ""
            room_id = f"{shisya}_{danchi}{shikibetu}_{room_id_raw}"

            floor = item.get("floor") or ""
            kai = f"{floor}／{floor_all}" if floor and floor_all else floor

            detail_link_path = item.get("roomDetailLink") or page_url
            detail_link = urllib.parse.urljoin(BASE_ORIGIN, detail_link_path)

            commonfee = item.get("commonfee") or ""

            rooms.append(
                {
                    "room_id": room_id,
                    "property_name": property_name,
                    "room_name": item.get("name") or "(部屋番号不明)",
                    "price": item.get("rent") or "(家賃不明)",
                    "commonfee": commonfee,
                    "madori": item.get("type") or "",
                    "area_text": html.unescape(item.get("floorspace") or ""),
                    "area_sqm": parse_area_sqm(item.get("floorspace")),
                    "kai": kai,
                    "detail_link": detail_link,
                    "page_url": page_url,
                }
            )

        # このページの件数が想定より少なければ、これ以上ページは無い
        row_max = data[0].get("rowMax")
        try:
            if row_max is not None and len(data) < int(row_max):
                break
        except (TypeError, ValueError):
            pass

    return rooms


# ------------------------------------------------------------
# 状態(通知済み)の管理
# ------------------------------------------------------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# ------------------------------------------------------------
# Discord通知
# ------------------------------------------------------------

def send_discord_notification(room: dict):
    if not DISCORD_WEBHOOK_URL:
        print("!! DISCORD_WEBHOOK_URL が設定されていないため通知をスキップしました")
        return

    rent_text = room["price"]
    if room["commonfee"]:
        rent_text += f"（共益費 {room['commonfee']}）"

    embed = {
        "title": f"🏠 新着空室: {room['property_name']}",
        "url": room["detail_link"],
        "color": 0x2E9CCA,
        "fields": [
            {"name": "物件名", "value": room["property_name"], "inline": False},
            {
                "name": "部屋番号/間取り",
                "value": f"{room['room_name']} / {room['madori']}（{room['area_text']}）",
                "inline": False,
            },
            {"name": "家賃", "value": rent_text, "inline": False},
            {"name": "UR公式リンク", "value": room["detail_link"], "inline": False},
        ],
    }
    if room["kai"]:
        embed["fields"].append({"name": "階数", "value": room["kai"], "inline": True})

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code >= 300:
            print(f"!! Discord通知に失敗しました status={resp.status_code} body={resp.text[:200]}")
    except requests.RequestException as e:
        print(f"!! Discord通知中にエラーが発生しました: {e}")


# ------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------

def main():
    # cron起動の間隔をランダムにばらつかせる(要件: 5〜10分程度のランダム待機)
    jitter_seconds = random.randint(0, 240)
    print(f"起動直後のランダム待機: {jitter_seconds}秒")
    time.sleep(jitter_seconds)

    state = load_state()
    new_state = dict(state)  # 更新後の状態(このチェックで見つかった全空室IDに置き換える)
    total_new = 0

    for property_name, url in TARGET_PROPERTIES:
        try:
            rooms = fetch_rooms_via_api(url, property_name)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            print(f"!! 取得失敗: {url} ({e})")
            # 取得失敗時は前回の状態を保持しておく(誤って空室リストを消さないため)
            if url in state:
                new_state[url] = state[url]
            continue

        # 80㎡以上の部屋だけを対象にする
        target_rooms = [r for r in rooms if r["area_sqm"] is not None and r["area_sqm"] >= MIN_AREA_SQM]

        previous_ids = set(state.get(url, []))
        current_ids = {r["room_id"] for r in target_rooms}
        new_state[url] = sorted(current_ids)

        new_rooms = [r for r in target_rooms if r["room_id"] not in previous_ids]

        if new_rooms:
            print(f"[{property_name}] 新着 {len(new_rooms)} 件 (条件: {MIN_AREA_SQM}㎡以上)")
        else:
            print(f"[{property_name}] 新着なし (対象 {len(target_rooms)} 件 / 全 {len(rooms)} 件)")

        for room in new_rooms:
            print(f"   -> 通知: {room['room_name']} {room['madori']} {room['area_text']} {room['price']}")
            send_discord_notification(room)
            total_new += 1
            time.sleep(1)  # Discordへの連続送信を少し間隔をあける

        # UR側への負荷軽減のため、物件ごとの取得間隔を少し空ける
        time.sleep(random.uniform(1.5, 4.0))

    save_state(new_state)
    print(f"完了: 新規通知 {total_new} 件")


if __name__ == "__main__":
    sys.exit(main())
