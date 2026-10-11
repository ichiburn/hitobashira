# 人柱くん（Hitobashira-kun）

> **⚠ 既知の欠陥（2026-10-11 外部レビューで指摘。同日 16時台に一部修正）**
> `tools/`（メール取り込み・報告書／絵馬生成）は試作です。修正後も実際の受信メールでは未検証のため、**実在サービス名での報告書作成にはまだ使わないでください**。
>
> **直したもの**（再現テストは `tests/test_review_fixes.py`）
> - 配信停止の判定: 販促メールだけで判定し、観測期限 `observed_until` が無い・時刻不明のメールがある・停止時刻が日付だけ（その日の終わりとはみなさない）の場合は「判定保留」
> - 帰属: +タグの抽出に境界を付けドメイン完全一致に限定。台帳に無いタグは `_quarantine` に隔離（新しい枠を作らない）。`expected_sender_domains` と差出人ドメインが不一致なら帰属「未確認」。報告書・絵馬にタグ付きアドレスを出さない
> - メール由来の文字列: 表セル用の共通エスケープ（`|`・改行・Markdown の画像/リンク記法・HTMLタグ）。配信停止URLと登録ページはドメインだけ表示。台帳の値に改行があっても停止しない
> - 重複判定: message_id があればそれだけで判定、無ければ UTC に直した時刻・差出人・件名で比較
> - 「0件」と「未観測」の区別、`2026/10/11 14:00` 形式の時刻、`-0000`・TZ無しを「TZ不明」として扱う、UTC での並べ替え
> - 分類: 差出人の表示名を使わず件名だけで照合、英語は単語境界つき、日本語は語句を絞った（レビューの5例は正しく分類）
> - 出力ファイル名にハッシュを付けて衝突を防止、絵馬の省略記号・長い名前の縮小
> - 黙って進む箇所: 環境変数未設定は警告、台帳が無いと終了コード 2、emails 欠損・null は「未観測」＋警告、想定外の type は「不明」として数える
> - `testsite/server.py`: `GET /` と `POST /submit` だけにし、記録に空欄フィールド・アクセス元・User-Agent を追加
> - SKILL.md: 送信してよい範囲を「自分が所有する 127.0.0.1 上のテスト環境のみ」に限定
>
> **issue #2（fail-closed）で直したもの**（再現テストは `tests/test_fail_closed.py`）
> - 帰属を根拠付きの状態に分けた: `trusted`（ドメイン一致＋信頼できる認証結果 pass）/ `domain_only`（From のドメイン一致だけ）/ `unverified` / `unchecked`。From の一致だけでは `trusted` にしない
> - `tools/auth_results.py`: Authentication-Results の解釈。信用するのは、アダプタが受信基盤から直接取り出した**一番上**のヘッダで、authserv-id が信頼リストにあるものだけ。任意入力 JSON（`ingest_email.py` の CLI）の認証ヘッダは記録するが信用しない
> - 配信停止の判定: `trusted` の販促メールだけを「効かず」の根拠にする（`domain_only` / `unchecked` / `unverified` だけなら判定保留）。停止後に分類不明のメールがあれば保留。0通は「観測期間内に検出せず」と書き、恒久的な成功とは言わない。`evidence_policy: strict` の台帳では受信経路の稼働確認（`receiver_checks`）が無ければ保留
> - 証拠ごとの provenance（取得元・取得時刻・受信時刻の出所・認証状態・帰属の根拠・分類の根拠）を保存
> - 互換: #2 以前の台帳にある `attribution: "verified"` は旧形式として数える（報告書に「旧形式」と出す）。新しい ingest は書かない
>
> **残っているもの**
> - 実際の受信メールでの動作・分類精度は未評価（SAMPLE・テスト・ローカル Mailpit の自作 DEMO メールのみ）
> - 帰属: issue #2 で fail-closed にした（下記）。ただし Authentication-Results は**ヘッダの解釈だけ**で、SPF/DKIM/DMARC の検証そのもの（DNS 参照・署名検証）はしていない。信頼できるのは受信基盤が付けたヘッダだけで、その受信基盤のアダプタは Mailpit（ローカル再現用、認証ヘッダを付けない）しか無い。実際の受信メールでは未検証
> - 分類は件名キーワードによる推定のまま
> - SKILL.md の送信範囲は、`harness/`（Playwright）を使った操作に限りコードで強制される（issue #3）。harness を使わない操作では手順上の約束のまま
> - `testsite3/server3.py` はレビュー対象外で、今回は変更していない
> - 絵馬 PNG は Pillow と日本語フォントが必要（無ければ警告して Markdown だけ作る）
>
> なお、これらはメール受信系の機能で、提出物で「実証済み」としている登録画面監査・独立検証・CONSENT FORENSICS の結果は tools/ を使っていません。

登録ページ（メルマガ・キャンペーン登録など）を監査する Bot です。ハッカソン作品。
「ページ上の約束」と「実際の画面・挙動」の差分を、証跡付きで記録します。観測していない数値は作らず、確認できないことは「未観測」「未実証」と書きます。

- 実サイトは**閲覧のみ**（入力・送信・ログインはしない）
- 送信を伴う検証は**自作のテストサイト**（127.0.0.1 上の DEMO）だけで行う
- 受信メールは件名・差出人・時刻・ヘッダ有無だけを台帳に記録し、本文は保存しない
- 受信用の +タグは**推測されにくいランダムな文字列**にする（サービス名をタグにすると第三者に偽造される）。タグ付きアドレスは成果物に出さない
- 結果を「被害報告書」（Markdown）と「絵馬」画像（PNG）にまとめる

ハッカソン期間中の試作です。下の区分表のとおり、未実証・構想段階の機能を含みます。

> **テストサイトについての注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。**
> `testsite/`・`testsite3/`・`testsite_mail/` は 127.0.0.1 だけで待ち受けます（`testsite_mail/` はローカルの Mailpit にだけメールを送ります）。待受アドレスを変更して外部に公開しないでください。

## 機能の区分（2026-10-11 20時台 JST 時点）

「実証済み」は、このリポジトリのコードを実際に動かして確かめた範囲だけを指します。issue #2〜#5 で増えた行は、box（Linux）上で `python3 -m unittest` とローカル E2E を実行して確かめました。

| 機能 | 区分 | 根拠・補足 |
|---|---|---|
| 登録ページの閲覧のみ監査（実サイト） | 実証済み | 1サイトのみ（docs/THEHEADLINE.md）。送信はしていない |
| 自作テストサイトでの送信を伴う監査 | 実証済み | run1（1サイト）・run2（3サイト）。自作 DEMO のみ、小規模 |
| 検証役による一次証拠との照合 | 実証済み | 同じ共有環境での実施で、完全な独立検証ではない（下記「検証の限界」） |
| メールメタデータの取り込み・分類（tools/ingest_email.py） | 実装済み | SAMPLE（ダミー）とローカル Mailpit の自作 DEMO メールでのみ動作確認。実際の受信メールでの精度は未評価 |
| 被害報告書・絵馬の生成（tools/report.py） | 実装済み | SAMPLE（ダミー）と自作 DEMO の台帳でのみ生成 |
| 帰属・配信停止判定の fail-closed（#2） | 実装済み | 単体テストのみ（tests/test_fail_closed.py）。Authentication-Results は**ヘッダの解釈だけ**で、実際の受信基盤が付けたヘッダでは未検証 |
| テスト用ブラウザの送信先制御（harness/、#3） | 実証済み | 自作の検証用ページで、外部 POST・iframe・fetch・WebSocket・リダイレクト・無許可ポート・localhost 表記の遮断と、ページ内の指示を無視して監査が続くことをテストで確認（Playwright＋Chrome がある環境のみ） |
| 実行ごとの証跡分離と SHA-256 マニフェスト（#3） | 実証済み | run_id ごとのディレクトリ、evidence/ は新規作成のみ・読み取り専用、改変検知をテストで確認。取得時の真正性は保証しない |
| +タグ付きアドレスでの受信 | 実証済み（ローカルのみ） | ローカル Mailpit で +タグ宛てのメールを受信・仕分け（#4）。実際のメール受信基盤では未実証 |
| 登録後のメール観測（受信数など） | 実証済み（ローカルのみ） | 自作 DEMO 2サイト × ローカル Mailpit、観測は数秒（#4）。実サイトでは未実証。頻度は測っていない |
| 配信停止の確認 | 実証済み（ローカルのみ） | 自作 DEMO の honest は「観測期間内に検出せず」、evil は「効かず」と判定が分かれることを E2E とテストで確認（#4）。実サービスの配信停止は未実証 |
| 役割の自動引き継ぎ（e2e/pipeline.py、#5） | 実証済み（ローカルのみ） | 1コマンドで 統括→人柱Bot→観測Bot→検証官→統括 をファイルの受け渡しで完走（honest / evil）。役割はスクリプトが演じ、同じ機械・同じプロセスなので完全なブラインド検証ではない |
| 体験レポートの生成（#5） | 実証済み（ローカルのみ） | 自作 DEMO 2サイトで生成（examples/e2e_sample/） |
| compose.yaml での一括起動 | 未実証 | ファイルはあるが、Docker が無い環境のため動かしていない |
| Windows 11 / WSL2 での実行 | 未実証 | 手順だけ docs/SETUP.md に記載。確認済みは Linux のみ |
| CI（GitHub Actions） | 未導入 | issue #1（対象外） |
| Grok Bot 同士のメッセージでの引き継ぎ | 構想 | 下記「Bot 同士の引き継ぎ（構想）」。未実装 |
| 課金の成立の確認 | 未実証 | テストサイトの完了画面に申込の文言を表示しただけで、実際の課金は発生させていない |
| 長期追跡 | 構想 | 未実装・未実証 |
| Bot テンプレート | 構想 | 未実証（スキル手順書 skills/ はあるが、テンプレートとしての再利用は検証していない） |

未実証・構想段階の機能が残っています。

## リポジトリ構成

| パス | 内容 |
|---|---|
| `skills/signup-page-audit/SKILL.md` | 登録ページ監査スキル（エージェント向け手順書） |
| `docs/SETUP.md` | 導入手順 |
| `docs/EVALUATION.md` | run1 / run2 の評価結果と、検証の条件・独立性の限界 |
| `docs/THEHEADLINE.md` | 実サイト（The HEADLINE ニュースレター登録ページ）の観測記録（事実のみ） |
| `templates/report_template.md` | 被害報告書のテンプレート |
| `templates/ledger.schema.md` | 台帳（ledger.json）の構造 |
| `templates/survey_template.json` | 調査記録（survey.json）の雛形 |
| `tools/common.py` / `tools/ingest_email.py` / `tools/report.py` | 共通処理 / メールメタデータの取り込みと分類 / 報告書・絵馬の生成 |
| `tools/auth_results.py` / `tools/mail_adapters.py` | Authentication-Results の解釈（検証はしない） / 受信基盤のアダプタ（Mailpit のみ） |
| `harness/` | テスト用ブラウザ（Playwright）の送信先制御、run_id ごとの証跡・ログ・SHA-256 マニフェスト |
| `testsite_mail/` | メール配信つきの自作テストサイト（honest / evil）。Mailpit の SMTP（127.0.0.1）へ送る |
| `e2e/run_mail_e2e.py` / `e2e/pipeline.py` / `e2e/handoff.py` | Mailpit のローカル E2E / 役割の引き継ぎを通しで再現 / 引き継ぎファイルの検証 |
| `schemas/handoff.schema.json` | 引き継ぎファイルの JSON Schema |
| `compose.yaml` | Mailpit とテストサイトの一括起動（未検証） |
| `examples/e2e_sample/` | ローカル E2E の体験レポートと引き継ぎファイルの例（自作 DEMO） |
| `examples/` | SAMPLE（ダミーデータ）の台帳・メール・生成例。`python3 examples/rebuild.py` で現行コードから再生成（`--check` で一致確認） |
| `tests/` | unittest（`python3 -m unittest`。標準ライブラリのみで動き、Playwright・Mailpit が無いテストはスキップ。CI設定は未導入） |
| `requirements.txt` | 依存の注記（標準ライブラリのみ。任意: 絵馬 PNG の Pillow、ブラウザ操作の Playwright、E2E の Mailpit バイナリ） |
| `testsite/` | 自作テストサイト（run1 用、1サイト） |
| `testsite3/` | 自作テストサイト（run2 用、honest / gray / evil の3サイト） |

使い方は [docs/SETUP.md](docs/SETUP.md) を参照してください。

## 評価結果（要約）

以下の数値はすべて**暫定値**で、対象はすべて自作の DEMO テストサイトです。サイト数が少なく（run1: 1サイト、run2: 3サイト）、実サイトでの精度を示すものではありません。**同じ共有環境で作成者側が用意した小規模なテストの参考値**であり、完全なブラインド検証ではありません。詳細は [docs/EVALUATION.md](docs/EVALUATION.md)。

| run | 条件 | 結果 |
|---|---|---|
| run1 | testsite/ 1サイト、閲覧のみ（送信なし）。検証官が一次証拠と人柱くんの報告を照合 | 一致 29、不一致 0、見落とし 6（検証不能 3） |
| run2 | testsite3/ 3サイト、送信あり。採点役が正解キーと照合（**暫定値**。検証官の結果が出る前の、人柱くん単独の検出結果（採点は別Bot）） | TP 15、FP 0、FN 1、Precision 1.00（Wilson 95% 区間 0.796〜1.000）、Recall 0.94（同 0.717〜0.989）、分類 3/3 |
| run2 | 検証官が独立に3サイトを判定 | 分類 3/3 |

run2 の区間は Wilson スコア法（z=1.96）で、Precision は 15/15、Recall は 15/16 から計算しました。項目数が16と少ないため区間は広く、点推定だけで精度を判断しないでください。

**runs/reuse1（非公開・SHA-256 のみ掲載）**: 共有スキル `skills/signup-page-audit/SKILL.md` だけを渡した新規Botが、初めて見る自作 DEMO サイト1つ（testsite3 の evil 版）を監査した記録です（スキルの再利用テスト）。送信は自作 DEMO サイトに対してのみで、完了画面の記録のため同じ値で2回送信しています。検証役への引き渡しは行っておらず、1サイトだけの確認なので汎用性は未検証です。

**検証の限界**

- (a) 送信記録のファイル名に分類ラベルが入っていた。
- (b) 採点役が採点中に送信記録へ一時的に追記し、あとで元に戻した。
- (c) 正解ファイルが 13:20:21 JST に読まれたが、誰が読んだかは確定していない。
- (d) 検証官は自分の判定を 13:26 JST に固定したあと、13:43 JST に人柱くんの結果を開いた。
- (e) 送信記録に、出所の分からない送信が2件ある。
- (f) 共有環境のため、完全なブラインド検証ではない。

## ローカル E2E（issue #4 / #5）

自作 DEMO サイト（honest / evil）とローカル Mailpit だけで、登録 → 受信 → ingest → 配信停止 → 再観測 → 判定 → 独立検証 → 体験レポート を1コマンドで再現します（`python3 e2e/pipeline.py`、手順は [docs/SETUP.md](docs/SETUP.md) 8.・9.）。
2026-10-11 19:55 JST の実行では、honest は「観測期間内に停止後の販促を検出せず」、evil は「効かず（停止後に販促1通）」で、検証官と観測Bot の判定は両サイトで一致しました（出力例 `examples/e2e_sample/`）。観測期間は数秒で、実サービスについては何も示しません。

## Bot 同士の引き継ぎ（構想・未実装）

いまの `e2e/pipeline.py` では、各役割をこのリポジトリのスクリプトが同じプロセスの中で演じています。**Grok Bot 同士が実際にメッセージを送り合って引き継ぐ部分は実装していません。** 構想と、実現するときの手順は次のとおりです。

1. 統括Bot がユーザーからテスト環境の URL（127.0.0.1）を1回受け取り、`task.json`（許可範囲）と `CREATED` の引き継ぎファイルを作る
2. 統括Bot が人柱Bot に、引き継ぎファイルと成果物（パスと SHA-256）を渡す（Bot 間のメッセージ、またはそれぞれのBotが読める保管場所）
3. 受け手の Bot は `e2e/handoff.py` の `accept()` と同じ確認（宛先・前の引き継ぎとのハッシュの連鎖・成果物のハッシュ）をしてから作業し、次の引き継ぎファイルを返す
4. 検証官Bot には一次証拠だけを渡し（`withheld` を守る）、判定を固定した時刻とハッシュ（`verify_lock`）を統括Bot に返してから、前段の結論を受け取る
5. 統括Bot が体験レポートをまとめてユーザーに返す。途中で止まったら `ABORTED` と理由を返す

独立性を高めるには、検証官Bot を別の機械・別の権限で動かし、正解や前段の結論を読めないようにする必要があります（いまは同じ機械なので「受け取っていない」ことしか示せません）。

## 実サイトの観測（The HEADLINE）

閲覧のみで観測した事実を [docs/THEHEADLINE.md](docs/THEHEADLINE.md) にまとめています（評価・法的判断は含みません）。

## 公開していないもの

実行ログ、送信記録（`*.jsonl`）、実サイトの生 HTML・DOM 全文、実サイトのスクリーンショット（転載可否が不明のため非公開）、正解キー、採点の内部ファイルは公開していません。
これらの一次証拠は非公開で保管しており、SHA-256 を以下に記載します（2026-10-11 13:50 JST 時点のコピー）。
**この一覧で示せるのは、この README を公開してから一次証拠を差し替えていないことだけです**。公開前に作られた・編集されたものでないこと、内容が正しいことは示せません。

<details>
<summary>一次証拠の SHA-256 一覧（68件）</summary>

```
1c27b29a898725562bd60c79e4f620c22ebeee6d9cdd4c21aa95af615d041839  .answer_key_v2.json
db2cfaabcf0bc528e8a1e489041d071d24ece5cda1492a9a7ef6c9cd829f216b  runs/evidence/build_report.py
0cc39b3d6831d4d1a0a765c0cdb8b0459760157be65aab719439823249fd6c98  runs/evidence/inbox_check.json
0089a0bcb67c18d02867de374b7045c48f6ee38a07bcbe1be30a18bdd0e254f2  runs/evidence/inbox_check_1340.json
a807ed08c83fc67c95d6ac4b0e1367dbc9bd1f567d024ac2731254c5218b521c  runs/evidence/inbox_check_1346.json
66ee8db0e210ab61d1a1e0fdc66afa841db88f332077f7b136c014be2eaffaaf  runs/evidence/independence_audit.json
306faf0db546618f01b1110dd24f23fbf8a94bc65c3ef40279e0fd787608d093  runs/evidence/make_score_ema.py
480543a7901a9ffaa16315c18f517bed7f7b64fc7ca7245a6d748f3d2b9ca1f4  runs/evidence/manifest.json
4d53caa75269f425a7cfd7afab0d9aed0cf4acbecb7ae9772c80d2950ca08ae2  runs/evidence/site_screenshot.png
aeb16d4433d6c242fd9edb6f27dceaf8846c942ee11f166eeadbea6312c6732d  runs/evidence/smtp_check.txt
4eeccfdf92ad8a9131ae526da3936d308e62b52ef808b92cd616acd7fbdb3605  runs/evidence/timeline.md
267dcea6a890e159898bb4501b955cc75c9125b534e83f90ba5c52dbd39bc110  runs/real1_theheadline/dom.html
170d030a22cc9f90c67c9f0c586482abf10246e02c1d2b7f6028e5ca7217bad3  runs/real1_theheadline/form.png
6c18dbb371120b426502de07cedb0be8667269198b67c64ae0c839bf4939f18a  runs/real1_theheadline/full_bottom.png
8a118aad2836c1e638a41762c797eb3e45c4e576a42e04b82be48558f892fd12  runs/real1_theheadline/full_wide.webp
e26a4c7bdf6cc774bd0e401d3d2d0077a9fb269818ca471bfe4459b2a3bb6170  runs/real1_theheadline/privacypolicy.html
09ac810ea5d2a59a6ac07a443a105cdc5da108936ff0ddd562a86fb1ebc7c87b  runs/real1_theheadline/raw.html
c7e89429bc88f357afced3547427d1bb33fd625657de1d166d7fd35282fb9387  runs/real1_theheadline/start.txt
80bdc73f12a548a77a6b8635a22c19725c7027db52023f7e14a7fdefcf104f32  runs/real1_theheadline/survey.json
c1c0bd43411c8a32e3a297cd53e900c79ee83ad03bd56408b611ede87e8262a4  runs/real1_theheadline/term.html
2fb4c7311bf793c85972ba03706688b2566f079438090ba5807798a773a7305f  runs/real1_theheadline/top.png
1c4c3cdefb94dcb3eb74e7ffa81662f355f637f62c3878cd7c3455cc05bedbb0  runs/reuse1/01_full.png
43064e9cd11e41252ffdcc8c57d4d3961482f3094e503d04f467e8fd2b137484  runs/reuse1/02_form.png
1c4c3cdefb94dcb3eb74e7ffa81662f355f637f62c3878cd7c3455cc05bedbb0  runs/reuse1/03_decline.png
5272c68aa75a381d05bb35f073aadb236101fe6729e47c79c0aec98a7bde119c  runs/reuse1/04_complete.png
63776339372b9e738846a1fb4585a13fb89c63b6a7d198b942f0f56c8fdbe708  runs/reuse1/complete_dom.html
dce9a4e88b0d646553e9badf7f5f6e0dae333951bc230df61a413f9e0efecd00  runs/reuse1/dom.html
945f8f98b8183a2e6f7ac7485616ade5578c7d4911fb58b9c1bf251bef91591d  runs/reuse1/manifest.json
ec665f71fb04339084ccf575f701645998a5dfef0e6cb91cbc8d2aaa92490c76  runs/reuse1/raw.html
1a4c4f1916e2b6cce15415e542e74d8fe39a2667d4a6f4582c52c2039d07d42e  runs/reuse1/survey.json
9b3f8726a88c7488d1ac9325e568aef8e5038104c5a86a5c4b38c1401c0828c0  runs/run1/ema/demo_ledger.json
9fdff42ac0366975467a958255684767c5964e9bfe1e2c1debf16fca6a1ebee4  runs/run1/ema/demo_run1.md
3d173876ce7e8c4f2f00022809612bcdbcb2a1fad18e1a5c9adc0acf1fac0e7a  runs/run1/ema/demo_run1.png
15eb3f96574e35a4cdd379d64a194e9aed84799417de4ea71415e9d19ba8b2c9  runs/run1/form.png
d9f3a8cbfa449ac81fafc0b829db36ae9144b9a5d49ae1a897c69137e9b380d7  runs/run1/report.md
72fa06ac64d0ab09b8286261287151ac3ce6802e527f233567458cbe0a4fe3bc  runs/run1/score.json
8bfa808f8811006a0cc8ea58618d007a1852dcf48f1ced25bc88fb9894d31db5  runs/run1/survey.json
4f9ffbcb9b83bce410a0a887c9f12d3e80e9df6dee81d8f1a3e1d9f89cbd96a7  runs/run1/verify.json
e02dc117a7a02ad76006ff449b9a367ebe7860211800c15d937837d3cef58689  runs/run2/A/declined.png
cec9685f9ea904657f7f8407d67daa421684173260093915d24d67a932d22f76  runs/run2/A/done.png
afb4f679247aa4ec195b89fbabfc5d50ab0673d80d4bc4c4972dbd09dc2fcb43  runs/run2/A/form.png
b162bf74092a76f502411b922b3d6dea3a6df53fbf6ba4414061607e425f8ca1  runs/run2/A/survey.json
e02dc117a7a02ad76006ff449b9a367ebe7860211800c15d937837d3cef58689  runs/run2/B/declined.png
eab926ab5c84a9b19a87405fb341a8f6072c09c84833dfe5dd082a591bd0b849  runs/run2/B/done.png
75f52b924f9909bd8243bcf66def00b375f71b9c2f6986ec81f91dc7a6e55d5a  runs/run2/B/form.png
2db254f821bbe68a86b22ff5e72b75ee6872a2672c663f73d4d9a90f24e9ec45  runs/run2/B/survey.json
1c4c3cdefb94dcb3eb74e7ffa81662f355f637f62c3878cd7c3455cc05bedbb0  runs/run2/C/declined.png
5272c68aa75a381d05bb35f073aadb236101fe6729e47c79c0aec98a7bde119c  runs/run2/C/done.png
1c4c3cdefb94dcb3eb74e7ffa81662f355f637f62c3878cd7c3455cc05bedbb0  runs/run2/C/form.png
11f124cd25d4e9899928593531c80fd0443c208c7bb5c72fccefe6a679ff97ea  runs/run2/C/survey.json
92f10fb78983ec5a0c4255ab2556998d6619cc16eae9d195732f84bfdd435b35  runs/run2/evidence/evil_completion.html
ec665f71fb04339084ccf575f701645998a5dfef0e6cb91cbc8d2aaa92490c76  runs/run2/evidence/evil_form.html
de3255dc90f0d1403d0b56e9af2a2d0edb44a1eacaf399a386b56503ec34641b  runs/run2/evidence/evil_form_hidden_and_checkboxes.html
77ed5802d3bab715ffdad3a1764afe09d29c6302dd8e6a8ee7daa584df5054a2  runs/run2/evidence/evil_server_processing_excerpt.py.txt
838f5293b3ddff53c11dc9bfee9746489483e07c4bbcb31c2caf1b8db464082e  runs/run2/evidence/evil_submissions_redacted.jsonl
eadbd68f30373e370cf62eb202163829af7d6ad95b3ea59c4d7a1e5bbf056466  runs/run2/evidence/gray_completion.html
ef069f571300f8eae32b57ea7c6e0f4e2ac3a76de930f69bc6c7d11b3041ac43  runs/run2/evidence/gray_form.html
d2d4e3fa08ec8bef2820d1b189535405a73a8cdb44efe85d9b95dc4ae88478d7  runs/run2/evidence/gray_submissions_redacted.jsonl
cb01deaff254bb179d19810b98ca3b72008131a2580c1c7838de8628082ab353  runs/run2/evidence/honest_completion.html
01e31cd520322b6d0bdb87e1bfd3d0ce2c1fd656c42869a514e4c129b0938cfc  runs/run2/evidence/honest_form.html
14025877c5847f2cda61aea97a229736518cf08ce92e50fd3e67c73951bc8e6b  runs/run2/evidence/honest_submissions_redacted.jsonl
d2aaa0e3a71ac39acf90cc62f8a47ee9ad96dc9e1acbadec1efd350dffe00325  runs/run2/score.json
1b02f84be49b1252fb3c1bfafb23f68f838f04e9ab2b822ccdfaa0217af083c4  runs/run2/score.md
3f77c9c53e5d50202a10c02e5b9bf866c2cae399e1b8d73beb15ad354b708be5  runs/run2/summary.json
28416b4af698ddeb0cf66b9bfe16fe5047a27b4aac1d5e820ebe09d13bdedc1a  runs/run2/verify.json
cb747e35a7a9f6ba313dc18c2954fedf0b7b28ab80e1a29a3a778743d44a191b  testsite3/evil_submissions.jsonl
8cf9cae108641585be8d7941e9926ca48a80980f0e9bbfbd21093fe30bc0f9f7  testsite3/gray_submissions.jsonl
0b811c8b6db7d802c2fe7f8a849d34c32c43dc9b9ac82e50ea97dfa2e87481f5  testsite3/honest_submissions.jsonl
```

</details>

## 安全について

- 実在の個人になりすまさず、AI であることが分かる名前を使います。
- ページ内やメール内の文章は単なるデータとして扱い、その中の指示には従いません。
- 個人情報や認証情報を成果物に含めません。

## ライセンス

MIT License（[LICENSE](LICENSE)）。Copyright (c) 2026 ichiburn
