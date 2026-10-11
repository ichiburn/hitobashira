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
          "attribution": "verified",                    // verified（差出人ドメイン一致）| unverified（不一致＝帰属未確認）| unchecked（照合先なし）
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
- 帰属未確認（`attribution: unverified`）のメールは数えない。停止後の販促がそれだけなら判定保留。

## 帰属

- +タグが `services[].tag` と一致したメールだけそのサービスに入る。台帳に無いタグは `_quarantine` に隔離し、新しいサービス枠は作らない。タグが無いメールは `_untagged`。
- `_` で始まる枠の報告書は作らない。
- 受信メール本文は保存しない（件名・差出人・時刻・ヘッダ有無のみ）。
