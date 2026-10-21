# 楽天市場 セカンドストリート新着監視

楽天市場商品検索APIで `2nd-street` の商品を新着順（`-updateTimestamp`）に直近30件取得し、
まだ通知していない商品（`itemCode` で判定）だけを Discord Webhook に通知します。

## エンドポイント

| パス | 内容 |
|---|---|
| `GET /` | ヘルスチェック |
| `GET/POST /check` | 1回チェックを実行し、結果をJSONで返す |

## 環境変数

| 名前 | 必須 | 説明 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | ○ | 通知先の Discord Webhook URL |
| `RAKUTEN_ACCESS_KEY` | ○ | 楽天ウェブサービスのアクセスキー（2026年の新API仕様で必須） |
| `RAKUTEN_REFERER` | △ | 楽天アプリ設定に登録したWebサイトURL。Refererチェックで403になる場合に設定 |
| `RAKUTEN_APP_ID` | | アプリケーションID（既定: `0d16e53b-cf1c-448e-aabe-5c49594bee17`） |
| `RAKUTEN_API_URL` | | APIエンドポイント（既定: `https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701`） |
| `STATE_BUCKET` | 推奨 | 通知済み itemCode を保存する GCS バケット名 |
| `STATE_OBJECT` | | GCS上のオブジェクト名（既定: `rakuten_2ndstreet_state.json`） |
| `STATE_FILE` | | `STATE_BUCKET` 未設定時のローカル保存先（既定: `/tmp/rakuten_2ndstreet_state.json`） |

Cloud Run のローカルディスクはインスタンスが入れ替わると消えるため、
重複通知を確実に防ぐには `STATE_BUCKET` を設定してください。

## デプロイ例

```bash
cd rakuten_2ndstreet

gcloud storage buckets create gs://YOUR_BUCKET --location=asia-northeast1

gcloud run deploy rakuten-2ndstreet-watcher \
  --source . \
  --region asia-northeast1 \
  --no-allow-unauthenticated \
  --max-instances 1 \
  --set-env-vars "DISCORD_WEBHOOK_URL=...,RAKUTEN_ACCESS_KEY=...,STATE_BUCKET=YOUR_BUCKET"
```

Cloud Run のサービスアカウントにはバケットへの `roles/storage.objectAdmin` を付与してください。

### 定期実行（Cloud Scheduler）

```bash
gcloud scheduler jobs create http rakuten-2ndstreet-check \
  --location asia-northeast1 \
  --schedule "*/10 * * * *" \
  --uri "https://<Cloud RunのURL>/check" \
  --http-method POST \
  --oidc-service-account-email <呼び出し用サービスアカウント>
```

## ローカル実行

```bash
pip install -r requirements.txt
DISCORD_WEBHOOK_URL=... RAKUTEN_ACCESS_KEY=... python main.py
curl localhost:8080/check
```
