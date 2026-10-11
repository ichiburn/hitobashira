# ledger.json 構造

```jsonc
{
  "version": 1,
  "bot_address": "yourbot@example.com",
  "sample": false,                 // true ならレポート/画像に SAMPLE 表示
  "services": [
    {
      "service": "service-a",                         // 表示名 兼 識別子
      "tag": "servicea",                              // +タグ（tag_address から自動導出も可）
      "tag_address": "yourbot+servicea@example.com",
      "signup_url": "https://...",
      "signed_up_at": "2026-10-11T13:05:00+09:00",       // ISO8601。未登録なら null
      "required_fields": ["email"],                      // 必須入力項目
      "prechecked_optins": ["パートナー企業からのお知らせ"], // 最初からチェック済みのオプトイン
      "dark_patterns": ["解約リンクが極小"],              // 観測したダークパターン（自由記述）
      "screenshots": ["screenshots/servicea_form.png"],
      "emails": [
        {
          "received_at": "2026-10-11T13:06:10+09:00",
          "from": "news@example.com",
          "subject": "ご登録ありがとうございます",
          "to": "yourbot+servicea@example.com",
          "type": "transactional",                       // transactional | marketing | other
          "has_list_unsubscribe": false
        }
      ],
      "unsubscribed_at": null,                           // 配信停止操作の時刻
      "emails_after_unsubscribe": null                   // 配信停止後の受信数（ingest/report が emails から再計算）
    }
  ]
}
```

- 値が null / 空配列の項目はレポートで「未観測」と表示される。
- 受信メール本文は保存しない（件名・差出人・時刻・ヘッダ有無のみ）。
