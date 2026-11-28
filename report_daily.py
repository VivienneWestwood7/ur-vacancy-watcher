"""
UR空室監視 日次レポート

GitHub Actionsの実行履歴(watch.yml)から、直近24時間の
実行回数・成功回数・失敗回数などを集計し、Discordに通知する。

監視スクリプト本体とは別のワークフローとして動くため、
本体が止まってしまった場合でも「実行0回」として気づくことができる。
"""

import datetime as dt
import os
import sys

import requests

# ------------------------------------------------------------
# 設定
# ------------------------------------------------------------

REPO = os.environ.get("GITHUB_REPOSITORY", "VivienneWestwood7/ur-vacancy-watcher")
WORKFLOW_FILE = "watch.yml"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

REPORT_HOURS = 24  # 集計する期間(時間)

# 次のどちらかに当てはまると「異常」として警告する
MIN_SUCCESS_RUNS = 100   # 24時間の成功回数がこれ未満 (正常時は約200〜290回)
MAX_GAP_MINUTES = 60     # 実行が1回も無かった時間がこれを超える (正常時は十数分以内)

JST = dt.timezone(dt.timedelta(hours=9))
REQUEST_TIMEOUT = 30


# ------------------------------------------------------------
# 実行履歴の取得・集計
# ------------------------------------------------------------

def parse_time(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def fetch_runs(since: dt.datetime):
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/runs"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    since_text = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = []
    for page in range(1, 11):  # 最大1000件
        resp = requests.get(
            url,
            headers=headers,
            params={"created": f">={since_text}", "per_page": 100, "page": page},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        batch = resp.json().get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            break
    return runs


def longest_gap_minutes(times, start, end) -> float:
    """期間内で、実行が1回も無かった最長の時間(分)"""
    points = [start] + sorted(times) + [end]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(points, points[1:])]
    return max(gaps)


def build_report():
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(hours=REPORT_HOURS)
    runs = fetch_runs(since)

    total = len(runs)
    success = sum(1 for r in runs if r.get("conclusion") == "success")
    failure = sum(1 for r in runs if r.get("conclusion") == "failure")
    cancelled = sum(1 for r in runs if r.get("conclusion") == "cancelled")
    running = sum(1 for r in runs if r.get("status") != "completed")

    times = [parse_time(r["created_at"]) for r in runs]
    gap = longest_gap_minutes(times, since, now)

    success_times = [
        parse_time(r["updated_at"]) for r in runs if r.get("conclusion") == "success"
    ]
    last_success = (
        max(success_times).astimezone(JST).strftime("%m/%d %H:%M") if success_times else "なし"
    )

    problems = []
    if success < MIN_SUCCESS_RUNS:
        problems.append(f"成功回数が少なすぎます({success}回 < {MIN_SUCCESS_RUNS}回)")
    if gap > MAX_GAP_MINUTES:
        problems.append(f"{int(gap)}分間、実行がありませんでした(基準: {MAX_GAP_MINUTES}分)")

    return {
        "since": since.astimezone(JST),
        "now": now.astimezone(JST),
        "total": total,
        "success": success,
        "failure": failure,
        "cancelled": cancelled,
        "running": running,
        "gap": gap,
        "last_success": last_success,
        "problems": problems,
    }


# ------------------------------------------------------------
# Discord通知
# ------------------------------------------------------------

def send_discord(report: dict):
    if not DISCORD_WEBHOOK_URL:
        print("!! DISCORD_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    ok = not report["problems"]
    fmt = "%m/%d %H:%M"
    embed = {
        "title": ("✅ " if ok else "⚠️ ") + "UR空室監視 日次レポート",
        "description": (
            f"集計期間: {report['since'].strftime(fmt)} 〜 {report['now'].strftime(fmt)} (直近{REPORT_HOURS}時間)"
        ),
        "color": 0x2ECC71 if ok else 0xE74C3C,
        "fields": [
            {"name": "実行回数", "value": f"{report['total']} 回", "inline": True},
            {"name": "成功", "value": f"{report['success']} 回", "inline": True},
            {"name": "失敗", "value": f"{report['failure']} 回", "inline": True},
            {
                "name": "キャンセル",
                "value": f"{report['cancelled']} 回(次の実行に置き換わったもの。異常ではありません)",
                "inline": False,
            },
            {"name": "最長の空白時間", "value": f"{int(report['gap'])} 分", "inline": True},
            {"name": "最後の成功", "value": report["last_success"], "inline": True},
        ],
    }
    if report["running"]:
        embed["fields"].append({"name": "実行中", "value": f"{report['running']} 回", "inline": True})

    payload = {"embeds": [embed]}
    if not ok:
        payload["content"] = "⚠️ **監視が正常に動いていない可能性があります**\n" + "\n".join(
            f"・{p}" for p in report["problems"]
        )

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()


def main():
    report = build_report()
    print(
        f"実行 {report['total']} / 成功 {report['success']} / 失敗 {report['failure']} / "
        f"キャンセル {report['cancelled']} / 最長空白 {int(report['gap'])}分"
    )
    for p in report["problems"]:
        print(f"!! {p}")
    send_discord(report)
    print("Discordに日次レポートを送信しました")


if __name__ == "__main__":
    sys.exit(main())
