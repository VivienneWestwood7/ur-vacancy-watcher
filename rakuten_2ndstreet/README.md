# 楽天市場 セカンドストリート新着監視

楽天市場商品検索APIで `2nd-street` の商品を新着順（`-updateTimestamp`）に直近30件取得し、
まだ通知していない商品（`itemCode` で判定）だけを Discord Webhook に通知します。

GitHub Actions（`.github/workflows/rakuten_2ndstreet.yml`）で10分ごとに無料で実行され、
通知済みの itemCode は `rakuten_2ndstreet/state.json` に記録されます。

## セットアップ

リポジトリの Settings → Secrets and variables → Actions → New repository secret で登録します。

| Secret名 | 必須 | 内容 |
|---|---|---|
| `RAKUTEN_ACCESS_KEY` | ○ | 楽天ウェブサービスのアクセスキー |
| `DISCORD_WEBHOOK_URL_2NDSTREET` | | 通知先の Discord Webhook URL。未登録ならUR監視と同じ `DISCORD_WEBHOOK_URL` を使う |
| `RAKUTEN_REFERER` | | 楽天アプリ設定に登録したWebサイトURL。楽天APIが403を返す場合に登録 |

スケジュール実行はデフォルトブランチ（main）にあるワークフローだけが動きます。
登録後、Actions タブの「セカンドストリート新着監視」→ Run workflow で手動実行して動作を確認してください。
初回は通知済みの記録がないため、直近30件がまとめて通知されます。

## ローカル実行

```bash
pip install -r requirements.txt
DISCORD_WEBHOOK_URL=... RAKUTEN_ACCESS_KEY=... python check_2ndstreet.py
```
