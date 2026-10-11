# ローカル E2E の出力例（自作 DEMO）

`python3 e2e/pipeline.py` を 2026-10-11 19:55 JST に box（Linux）で実行したときの出力の一部です。対象は自作の DEMO テストサイト（127.0.0.1）だけで、実在のサービスの観測ではありません。

| ファイル | 内容 |
|---|---|
| `report_honest.md` | 体験レポート（配信停止が効くサイト） |
| `report_evil.md` | 体験レポート（停止完了と言いながら販促を送り続けるサイト） |
| `handoff_03_verifier_to_coordinator.json` | 検証官 → 統括 の引き継ぎ（判定を固定した時刻とハッシュ `verify_lock`） |
| `compare_evil.json` | 検証官が判定を固定したあとで観測Bot の結論と比べた結果 |

生の証拠（Mailpit のヘッダのスナップショット・HTML・スクリーンショット・ログ）は `runs_local/`（`.gitignore` 済み）に残り、ここには含めていません。テスト用アドレス（+タグ）・本文・Received ヘッダはレポートに出しません。
