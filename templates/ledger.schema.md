# ledger.json 構造

```jsonc
{
  "version": 1,
  "sample": false,                 // true ならレポート/画像に SAMPLE 表示
  "services": [
    {
      "service": "service-a",                         // 表示名 兼 識別子（"_" で始まる名前は予約: _quarantine / _untagged）
      "tag": "k7q2x9fm",                              // +タグ。推測されにくいランダムな文字列にする（サービス名を使わない）
      "tag_address": "yourbot+k7q2x9fm@example.com",  // 台帳内だけに保存。報告書・絵馬には出さない
      "expected_sender_domains": ["news.service-a.example"], // 差出人ドメインの照合先（サブドメインも一致扱い）。空なら照合しない
      "signup_url": "https://...",                    // 報告書にはドメインだけ出す
      "signed_up_at": "2026-10-11T13:05:00+09:00",    // ISO8601（TZ付き）。未登録なら null
      "observed_until": "2026-10-12T13:05:00+09:00",  // 受信を観測した期限（TZ付き）。配信停止の判定に必須
      "required_fields": ["email"],                   // 必須入力項目
      "prechecked_optins": [],                        // 最初からチェック済みのオプトイン
      "dark_patterns": ["解約リンクが極小"],           // 観測したダークパターン（自由記述）
      "screenshots": ["screenshots/servicea_form.png"],
      "evidence_policy": "strict",                    // ingest が付ける（記録用）。値が無い台帳も strict として扱い、受信経路の稼働確認が無いと「検出せず」と言わない
      "receiver_checks": [                            // 任意: 受信経路の稼働確認（アダプタが記録）。停止前と observed_until 以降の両方に ok が必要
        {"at": "2026-10-11T13:00:00+09:00", "kind": "canary", "ok": true}
      ],
      "unsubscribe_url": "https://...",               // 任意: 最初に取り込んだメールの配信停止URL。保存のみ・開かない。報告書はドメインのみ
      "notes": ["..."],                               // 任意: メモ（例: List-Unsubscribe を本文リンクで代替判定した旨）
      "emails": [
        {
          "received_at": "2026-10-11T13:06:10+09:00",   // TZ付き推奨。TZ無し・-0000 は「TZ不明」
          "received_at_status": "tz_unknown",           // 任意: 時刻を確定できない場合だけ（missing / tz_unknown / date_only / invalid）
          "from": "news@news.service-a.example",        // 改行は空白に正規化して保存
          "subject": "ご登録ありがとうございます",
          "to": "yourbot+k7q2x9fm@example.com",
          "type": "transactional",                      // transactional | marketing | other
          "has_list_unsubscribe": false,
          "lu_source": "header",                        // header | body_link（ヘッダが取れず本文のリンク有無で代替）| null
          "attribution": "domain_only",                 // trusted（ドメイン一致＋信頼できる認証結果 pass、または自己所有の閉じた環境）
                                                        // | domain_only（ドメイン一致のみ。From は偽装できる）| unverified（不一致・認証 fail）
                                                        // | unchecked（照合先なし）| verified（旧形式。信用しない＝数えない。報告書は「旧形式のため未確認」。新しい ingest は書かない）
          "provenance": {                               // 証拠ごとの出所（ingest が付ける）
            "source": "input_json",                     // input_json（任意入力）| mailpit_api など受信基盤のアダプタ
            "ref": null, "fetched_at": null, "ingested_at": "2026-10-11T13:07:00+09:00",
            "received_at_source": "input_json.received_at",
            "auth": {"status": "none", "trusted": false, "authserv_id": null, "spf": null, "dkim": null, "dmarc": null, "basis": "..."},
            "attribution_basis": "...", "type_basis": "subject_keyword_estimate"
          },
          "message_id": "<...>",                        // 任意: 重複判定に使う（あればこれだけで判定）
          "thread_id": "...",                           // 任意
          "unsubscribe_url": "https://...",             // 任意: 保存のみ・開かない
          "quarantine_tag": "..."                       // _quarantine 内のメールだけ: 台帳に無かったタグ
        }
      ],
      "unsubscribed_at": null,                          // 配信停止操作の時刻（TZ付き）
      "emails_after_unsubscribe": null                  // 停止後の販促メール数（ingest が再計算。判定保留・未観測なら null）
    }
  ]
}
```

## 「0件」と「未観測」

- `null` またはキーが無い → 報告書では「未観測」。
- 空配列 `[]` → `signed_up_at` があれば「なし（0件）」（観測して無かった）、無ければ「未観測」。
- `emails: []` は `observed_until` があれば「0通（〜まで観測）」、無ければ「未観測」。

## 配信停止の判定（tools/common.py `unsub_verdict`）

- 販促メール（`type: marketing`）だけで判定する。停止完了通知などの手続きメールは数えない。
- `unsubscribed_at` が無い → 未観測。日付だけ（例 `2026-10-11`）・TZ不明・解釈不能 → **判定保留**（その日の終わりとはみなさない）。
- `observed_until` が無い、または停止時刻以前 → 判定保留。
- 受信時刻が不明なメールが1通でもある → 判定保留。
- 数えるのは帰属が `trusted` のメールだけ。`domain_only` / `unchecked` / `unverified` / 旧形式の `verified` は「効かず」の確定根拠にしない。停止後の販促がそれだけなら判定保留。
- 停止後に分類 `other`・想定外のメールがある → 判定保留（0通と断定しない）。
- 停止後の販促を検出しなかった場合の表現は「観測期間内に検出せず」（`effective`）。恒久的な停止成功ではない。`evidence_policy` の有無にかかわらず、`receiver_checks` で停止前と観測期限後の受信経路の稼働を確認できなければ判定保留。

## Authentication-Results の扱い（tools/auth_results.py）

- ヘッダの**解釈だけ**で、SPF/DKIM/DMARC の検証（DNS 参照・署名検証）はしない。
- 信用するのは、アダプタが受信基盤から直接取り出したヘッダのうち、**一番上**の Authentication-Results で、authserv-id が信頼リストにあるものだけ。任意入力 JSON（ingest_email.py の CLI）の値は解釈して記録するが信用しない。
- DMARC pass（header.from が From と整合）、または From と整合する DKIM（header.d）/ SPF（smtp.mailfrom）の pass → `trusted`。信頼できる結果が fail → `unverified`。整合の判定は公開サフィックスリストを使わない近似。

## 帰属

- +タグが `services[].tag` と一致したメールだけそのサービスに入る。台帳に無いタグは `_quarantine` に隔離し、新しいサービス枠は作らない。タグが無いメールは `_untagged`。
- `_` で始まる枠の報告書は作らない。
- 受信メール本文は保存しない（件名・差出人・時刻・ヘッダ有無のみ）。
