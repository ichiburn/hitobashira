# 導入手順

## 必要なもの

- Python 3.10 以降
- Pillow（絵馬画像の生成に使用）: `pip install pillow`
- 日本語フォント（例: Debian/Ubuntu なら `sudo apt-get install -y fonts-noto-cjk`）

## 1. 受信アドレスを設定する

人柱くんは「サービスごとに +タグ を付けた自分専用アドレス」で登録し、届いたメールをタグで仕分けます。
※ +タグでの実際の受信は未実証です（設計上の想定。タグの抽出・仕分けは SAMPLE データでのみ動作確認）。
自分が管理するアドレスを環境変数で指定してください（既定値は例示用の `yourbot@example.com`）。

```bash
export HITOBASHIRA_BOT_LOCAL=yourbot
export HITOBASHIRA_BOT_DOMAIN=example.com
```

## 2. 台帳（ledger.json）を用意する

構造は `templates/ledger.schema.md` を参照。空の台帳は最初の `ingest_email.py` 実行時に自動作成されます。
`ledger.json` は個人の観測データなので `.gitignore` 済みです。

## 3. メールのメタデータを取り込む（本文は渡さない）

```bash
cd tools
python3 ingest_email.py ../examples/sample_mails/m1.json --ledger /tmp/ledger.json
echo '{"from":"news@example.com","subject":"ご登録ありがとうございます","to":"yourbot+servicea@example.com","received_at":"2026-10-11T13:00:00+09:00","has_list_unsubscribe":false}' \
  | python3 ingest_email.py - --ledger /tmp/ledger.json
```

## 4. 報告書を生成する

```bash
cd tools
python3 report.py --ledger ../examples/sample_ledger.json --out /tmp/out
```

`out/<tag>.md`（被害報告書）と `out/<tag>.png`（絵馬画像）ができます。生成例は `examples/out/`（SAMPLE・ダミーデータ）にあります。
報告書の構成は `templates/report_template.md`、調査記録の形式は `templates/survey_template.json` を参照してください。

## 5. 監査スキル

`skills/signup-page-audit/SKILL.md` がエージェント向けの監査手順です。実サイトでは閲覧のみ（入力・送信・ログインはしない）、送信は自作テスト環境だけで行います。

## 6. 自作テストサイト（DEMO）を動かす

> **注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。**

すべて 127.0.0.1 だけで待ち受けます（変更しないでください）。実在のサービスではありません。

```bash
# run1 用: 1サイト（ポート 8765）
cd testsite && python3 server.py
# run2 用: 3サイト（honest=8766 / gray=8767 / evil=8768）
(cd testsite3 && python3 server3.py honest & python3 server3.py gray & python3 server3.py evil & wait)
# または: cd testsite3; python3 server3.py honest & python3 server3.py gray & python3 server3.py evil &
```

POST された内容はスクリプトと同じフォルダの `*_submissions.jsonl`（testsite は `submissions.jsonl`）に、アクセスログは `*_access.log` に書かれます。これらは `.gitignore` 済みで、公開しないでください。
なお、サイト名（honest / gray / evil）がファイル名に入るため、ブラインド評価に使う場合はファイル名から分類が分かる点に注意してください（docs/EVALUATION.md の限界 (a)）。
