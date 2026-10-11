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

## 7. テスト用ブラウザの送信先制御（harness/、issue #3）

`harness/` は人柱くんのテスト操作用の Playwright（python）ハーネスです。SKILL.md の「送信は自作の 127.0.0.1 だけ」を、手順上の約束ではなく**コードで強制**します。

- `harness/egress.py`: 許可判定。http・ホスト文字列 `127.0.0.1`・許可ポートだけを通し、書き込み（GET/HEAD 以外）はポートごとの許可パスだけ。`localhost`・`::1`・`127.0.0.2`・10進表記・認証情報付き URL・WebSocket は拒否
- `harness/browser.py`: `context.route("**/*")` で全リクエスト（ページ・iframe・fetch/XHR・フォーム送信）を判定し、許可外は遮断してログに残す。許可したものは `max_redirects=0` で取得し、リダイレクト先も判定する。WebSocket は接続させず、Service Worker はブロック。Chromium には到達しないプロキシ（127.0.0.1:9）を指定し、ループバック以外への直接通信も止める（多重の防御）
- ページの文章は非信頼データ。「監査を無視しろ」などの指示は `untrusted_instructions_detected` に記録するだけで、監査は続ける
- `harness/runctx.py`: 実行ごとに `run_id` を振り、`<出力先>/<run_id>/` に `evidence/`（生の証拠。新規作成のみ・書いたら読み取り専用）、`verdict/`（判定。証拠とは別ファイル）、`log.jsonl`（許可判定・操作・証拠パス・判定時刻）、`manifest.json`（SHA-256）を作る。**SHA-256 は固定後の改変検知で、取得時の真正性は保証しない**

Playwright は任意です。無い環境では `tests/test_harness_browser.py` はスキップされます。

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install playwright
python3 -m playwright install chromium     # 既に Google Chrome があれば不要（HITOBASHIRA_CHROME で実行ファイルを指定可）
python3 -m unittest -v tests.test_egress tests.test_harness_browser
```

### Windows 11 / WSL2 での実行

- **WSL2（Ubuntu）での実行を推奨**します。上の Linux の手順がそのまま使えます。`python3 -m playwright install --with-deps chromium` でブラウザと依存ライブラリを入れてください（sudo を求められます）
- テストサイト・Mailpit・ブラウザは**すべて WSL2 の中で**動かしてください。WSL2 の 127.0.0.1 と Windows 側の 127.0.0.1 は別のネットワークです（localhost 転送の設定によっては Windows から見えることがありますが、ハーネスは WSL2 内の 127.0.0.1 だけを許可します）
- `.wslconfig` の `networkingMode=mirrored` を使っている場合も、待受アドレスは 127.0.0.1 のままにし、`0.0.0.0` に変えないでください
- Windows ネイティブ（PowerShell）の場合: `py -m venv .venv; .venv\Scripts\Activate.ps1; pip install playwright; python -m playwright install chromium; python -m unittest -v`。Mailpit は Windows 版のバイナリ（公式 GitHub Releases）を使い、`--listen 127.0.0.1:<port> --smtp 127.0.0.1:<port>` で起動してください
- 既知の癖: 遮断で失敗したページを同じページで再度 goto すると Playwright が応答しなくなることがあった（Chrome 154 / Playwright 1.63）。遮断後はページを閉じて新しいページを使う
- Windows / WSL2 上での実行は、このリポジトリの作成者は**未確認**です（確認済みは Linux のみ）

## 8. Mailpit でのローカル E2E（e2e/、issue #4）

> **注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。** Mailpit は本物の配信サービスの代わりではなく、再現用のローカル受信箱です。

`testsite_mail/server_mail.py` は、登録時に Mailpit の SMTP（127.0.0.1）へ確認メール（手続き）と販促メールを送る DEMO サイトです。`honest`（配信停止が効く）と `evil`（停止完了と言いながら販促を送り続ける）の2つのモードがあります。

1本のスクリプトで、各サイトについて次を無人で実行します。

```bash
# Mailpit のバイナリ（公式 GitHub Releases の linux-amd64）を用意する。例:
curl -sSL -o /tmp/mailpit.tar.gz https://github.com/axllent/mailpit/releases/latest/download/mailpit-linux-amd64.tar.gz
sha256sum /tmp/mailpit.tar.gz      # リリースページの digest と照合する
tar -xzf /tmp/mailpit.tar.gz -C /tmp mailpit
python3 e2e/run_mail_e2e.py        # honest と evil を通しで実行（MAILPIT_BIN / --mailpit-bin でも指定可）
```

| 段階 | 内容 | 証跡（`runs_local/<run_id>/`） |
|---|---|---|
| 登録 | フォームの事前同意状態（チェックの初期値）を保存してから、初期状態のまま送信 | `evidence/01_consent_state.json`, `01_signup_form.*`, `01_register_response.html` |
| 受信 | Mailpit の API からヘッダとメタデータだけを取得（本文・`Received` は保存しない）。カナリアで受信経路を確認 | `evidence/02_mailpit_before_unsub.json`, `log.jsonl`（receiver_check） |
| ingest | `ingest_email.ingest()` に Mailpit アダプタの provenance を付けて取り込み | `derived/03_ledger_after_ingest.json` |
| 配信停止 | List-Unsubscribe の URL を（許可判定を通してから）開き、停止ボタンを押す | `evidence/04_unsubscribe_page.html`, `04_unsubscribe_response.html` |
| 再観測 | 配信トリガー（`/_test/campaign`）→ 数秒待つ → `observed_until` → カナリアで受信経路を確認 → 取り直して ingest | `evidence/05_campaign_trigger.json`, `05_mailpit_after_unsub.json`, `derived/06_ledger_final.json`, `derived/unsub_observation.json` |
| 判定 | `unsub_verdict()` と報告書（証拠とは別ファイル） | `verdict/07_unsub_verdict.json`, `verdict/07_report.md`, `manifest.json` |

- 帰属: Mailpit は認証ヘッダを付けないため、このE2Eでは「自分で起動した Mailpit に自分で起動したテストサイトだけが送る」ことを根拠に `trusted` にしている（`closed_env`）。`--no-closed-env-trust` を付けると帰属は `domain_only` になり、evil でも「判定保留」になる（認証不明のとき断定しないことの確認）
- 観測期間は数秒で、時間経過の代わりに配信トリガーを叩いている。実サービスでの配信停止の実証ではない
- Playwright があればブラウザ（harness/）で操作し、無ければ urllib で同じ許可判定をしてから送る（`--no-browser`）
- テスト: `python3 -m unittest tests.e2e.test_mail_e2e`（Mailpit が無ければスキップ）
- `compose.yaml` で Mailpit とテストサイトをまとめて起動することもできる（**未検証**。Docker Desktop / WSL2 / Ubuntu いずれでも作成者は試していない）。`python3 e2e/run_mail_e2e.py --mailpit-api http://127.0.0.1:8025 --mailpit-smtp-port 1025` で既存の Mailpit を使う
- Windows 11: WSL2（Ubuntu）の中で上の手順をそのまま実行するのを推奨。Docker Desktop を使う場合は WSL2 バックエンドで `docker compose up -d` し、E2E スクリプトも WSL2 内で実行する（未検証）。Windows ネイティブでは Mailpit の Windows 版バイナリを `--mailpit-bin` で指定する（未検証）

## 9. 役割の引き継ぎと体験レポート（e2e/pipeline.py、issue #5）

1つのコマンドで、統括 → 人柱Bot（調査・登録）→ 観測Bot（受信・配信停止・再観測）→ 検証官（独立判定）→ 統括（体験レポート）を通しで再現します。各役割はこのリポジトリのスクリプトが演じます。

```bash
python3 e2e/pipeline.py                       # DEMO の honest / evil を起動して通しで実行（Mailpit は 8. と同じく自動起動）
# 起動済みのテストサイト（testsite_mail 互換）に URL を1回渡す場合:
python3 e2e/pipeline.py --target http://127.0.0.1:8781/ --sender-domain honest-news.test \
  --mailpit-api http://127.0.0.1:8025 --mailpit-smtp-port 1025
```

- 引き継ぎファイル: `handoff/NN_<from>_to_<to>.json`（スキーマ `schemas/handoff.schema.json`、検証は `e2e/handoff.py` の `validate()`）。新規作成のみで、`prev_handoff_sha256` で前の引き継ぎにつながる。受け手は `accept()` で宛先・ハッシュの連鎖・成果物のハッシュを確かめてから作業する
- 状態の遷移: `CREATED`（統括が許可範囲を固定）→ `SURVEYED`（人柱Bot）→ `OBSERVED`（観測Bot）→ `VERIFIED`（検証官。`verify_lock` に判定の固定時刻と SHA-256）→ `REPORTED`（統括）。途中で失敗したら `ABORTED`（理由つき）を書いて止まり、隠さない
- 検証官に渡すのは一次証拠（`evidence/`）と `task.json` だけ。観測Bot の結論・台帳・集計は `withheld` に書いて渡さない。検証官は一次証拠から台帳を作り直して判定を固定し、そのあとで観測Bot の結論を開いて `compare.json` に比較を書く
- 体験レポート `verdict/report.md`: 約束と現実の差分、引き継ぎの時刻とハッシュ、実測の実行時間・受信数・問題検出数、実施していない工程、検証の条件と限界
- 同じ機械・同じプロセスで動くので、**完全なブラインド検証ではない**。検証官が前段の結論を「受け取っていない」ことは示せるが、「読めなかった」ことまでは示せない
- 出力例: `examples/e2e_sample/`
