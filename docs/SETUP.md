# 導入手順

## 必要なもの

- Python 3.10 以降（取り込み・報告書 Markdown・テストは標準ライブラリのみ。`requirements.txt` 参照）
- 任意: Pillow と日本語フォント（絵馬 PNG を作る場合だけ）: `pip install pillow`、Debian/Ubuntu なら `sudo apt-get install -y fonts-noto-cjk`。無い場合は警告を出して PNG を作らない（終了コード 1）

## 1. 受信アドレスを設定する

人柱くんは「サービスごとに +タグ を付けた自分専用アドレス」で登録し、届いたメールをタグで仕分けます。
※ +タグでの実際の受信は未実証です（設計上の想定。タグの抽出・仕分けは SAMPLE データでのみ動作確認）。
自分が管理するアドレスを環境変数で指定してください（既定値は例示用の `yourbot@example.com`）。

```bash
export HITOBASHIRA_BOT_LOCAL=yourbot
export HITOBASHIRA_BOT_DOMAIN=example.com
```

未設定のまま実行すると警告を出します（例示用アドレスで照合するため、実際のメールは全部 `_untagged` に入ります）。

**+タグは推測されにくいランダムな文字列にしてください**（例: `python3 -c "import secrets;print(secrets.token_hex(5))"` → `yourbot+3f9a1c07be@...`）。
サービス名をそのままタグにすると、第三者がそのタグ宛てにメールを送って結果を偽造できます。
タグ付きアドレスは報告書・絵馬には出しません（台帳 ledger.json の中だけに保存）。
さらに台帳の `services[].expected_sender_domains` に差出人ドメインを書いておくと、一致しないメールは帰属「未確認」になり、配信停止の判定に数えません。
台帳に無いタグのメールは `_quarantine` に隔離され、新しいサービス枠は自動では作られません。
## 2. 台帳（ledger.json）を用意する

構造は `templates/ledger.schema.md` を参照。サービス枠（`service` / `tag` / `expected_sender_domains` / `signed_up_at` / `observed_until` など）は手で書きます。
台帳ファイルが無いと `ingest_email.py` はエラー（終了コード 2）で止まります。新規作成するときだけ `--create-ledger` を付けてください。
`ledger.json` は個人の観測データなので `.gitignore` 済みです。

## 3. メールのメタデータを取り込む（本文は渡さない）

```bash
cd tools
cp ../examples/sample_base.json /tmp/ledger.json
python3 ingest_email.py ../examples/sample_mails/m1.json --ledger /tmp/ledger.json
echo '{"from":"news@example.com","subject":"ご登録ありがとうございます","to":"yourbot+sample-kaiun@example.com","received_at":"2026-10-11T13:00:00+09:00","has_list_unsubscribe":false}' \
  | python3 ingest_email.py - --ledger /tmp/ledger.json
```

## 4. 報告書を生成する

```bash
cd tools
python3 report.py --ledger ../examples/sample_ledger.json --out /tmp/out
```

`out/<タグ等>_<ハッシュ10桁>.md`（被害報告書）と `.png`（絵馬画像）ができます。ファイル名のハッシュはサービス名とタグから作るので、似た名前でも重なりません。
台帳が無いとエラー（終了コード 2）、PNG を作れなかったときは警告（終了コード 1）です。`--generated-at` で生成日時の表記を固定できます。
生成例は `examples/out/`（SAMPLE・ダミーデータ）にあります。`python3 examples/rebuild.py` で現行コードから作り直せ、`--check` でコミット済みの内容と一致するか確認できます。

## 4.5 テスト

```bash
python3 -m unittest -v      # リポジトリ直下で。標準ライブラリのみ
```
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

`testsite/server.py` は `GET /`（フォーム）と `POST /submit`（記録）だけを受け付け、それ以外は 404/405 を返します（フォルダ内のファイルは配信しません）。記録には時刻・アクセス元・User-Agent・全フィールド（空欄も含む）を残します。
POST された内容はスクリプトと同じフォルダの `*_submissions.jsonl`（testsite は `submissions.jsonl`）に、アクセスログは `*_access.log` に書かれます。これらは `.gitignore` 済みで、公開しないでください。
なお、サイト名（honest / gray / evil）がファイル名に入るため、ブラインド評価に使う場合はファイル名から分類が分かる点に注意してください（docs/EVALUATION.md の限界 (a)）。
