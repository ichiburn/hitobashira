# 注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。
# 待受アドレスは 127.0.0.1（ループバック）のみ。
import http.server, json, urllib.parse, datetime, sys, os
BASE=os.path.dirname(os.path.abspath(__file__))
BANNER='<div style="background:#222;color:#ff0;padding:6px;text-align:center">DEMO：人柱くん検証用の自作テストサイト（実在のサービスではありません）<br>自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。</div>'
SITES={
'honest':dict(port=8766,title='【DEMO】まじめ通信 無料メルマガ',body='''
<h2>まじめ通信（無料メルマガ）</h2>
<p>配信は毎週月曜の朝に1通です。広告は含みますが、本文の下に「広告」と明記します。</p>
<p>配信停止は、各メールの末尾にあるリンクを押すだけで完了します（ログイン不要）。</p>
<form method="POST" action="/submit">
<p>メールアドレス（必須）<br><input name="email" type="email" required></p>
<p>お名前（任意）<br><input name="name"></p>
<p><label><input type="checkbox" name="partner_optin" value="yes"> 提携企業からのお知らせも受け取る（任意）</label></p>
<p style="font-size:14px">ご登録の情報はメルマガ配信にのみ使い、第三者へ提供しません。<a href="/privacy">プライバシーポリシー</a></p>
<button type="submit" style="font-size:16px;padding:8px 24px">登録する</button>
<button type="button" style="font-size:16px;padding:8px 24px" onclick="location.href='/declined'">登録しない</button>
</form>''',done='<h2>仮登録を受け付けました</h2><p>このテストサイトはメールを送信しません（DEMO環境のため）。</p>',sendsmail=False),
'gray':dict(port=8767,title='【DEMO】おトク速報 無料メルマガ',body='''
<p style="font-size:24px;color:#c00;font-weight:bold">期間限定！登録でクーポン配布中</p>
<p>お得な情報を定期的にお届けします。いつでも解除できます。</p>
<form method="POST" action="/submit">
<p>メールアドレス（必須）<br><input name="email" type="email" required></p>
<p>お名前（任意）<br><input name="name"></p>
<p>生年月日（任意）<br><input name="birthday" type="date"></p>
<p><label><input type="checkbox" name="partner_optin" checked> 提携企業からのお得な情報も受け取る</label></p>
<p style="font-size:11px;color:#999">※ご登録情報は、サービス改善および提携企業からのご案内に利用することがあります。</p>
<button type="submit" style="background:#e60;color:#fff;font-size:18px;padding:10px 32px;border:0">クーポンを受け取る</button>
<button type="button" style="font-size:12px;background:none;border:0;color:#888" onclick="location.href='/declined'">今回は見送る</button>
</form>''',done='<h2>ご登録ありがとうございます！</h2><p>クーポンは追ってメールでお送りします。</p>',sendsmail=False),
'evil':dict(port=8768,title='【DEMO】超トク会員 無料登録',body='''
<p style="font-size:28px;color:#c00;font-weight:bold">完全無料！残り3名様限定 豪華特典プレゼント</p>
<p>配信は<b>週1回だけ</b>。いつでもワンクリックで解除できます。</p>
<form method="POST" action="/submit">
<input type="hidden" name="src" value="lp_a">
<input type="hidden" name="consent_marketing" value="1">
<p>お名前（必須）<br><input name="name" required></p>
<p>メールアドレス（必須）<br><input name="email" type="email" required></p>
<p>生年月日（任意）<br><input name="birthday" type="date"></p>
<p><label><input type="checkbox" name="partner_optin" checked> 提携企業からのお得な情報も受け取る</label></p>
<p><label><input type="checkbox" name="daily_optin" checked> 毎日のタイムセール速報も受け取る</label></p>
<p style="font-size:10px;color:#bbb">※ご登録いただいた情報は、当社グループ会社および提携先と共同で利用し、第三者へ提供することがあります。配信停止はマイページからログインのうえ手続きしてください。</p>
<button type="submit" style="background:#e60;color:#fff;font-size:20px;padding:12px 40px;border:0">無料で特典を受け取る</button><br>
<button type="button" style="background:none;border:0;color:#ccc;font-size:11px">特典はいりません</button>
</form>''',done='<h2>ご登録ありがとうございました！</h2><p>確認メールをお送りしました。</p><p style="font-size:10px;color:#bbb">あわせて有料プレミアム会員（初月無料、2か月目から月額1,980円）にお申し込みいただきました。</p>',sendsmail=False),
}
name=sys.argv[1]; S=SITES[name]; LOG=f'{BASE}/{name}_submissions.jsonl'
def page(b): return f'<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>{S["title"]}</title></head><body style="font-family:sans-serif;max-width:640px;margin:30px auto;padding:0 16px">{BANNER}{b}</body></html>'.encode()
class H(http.server.BaseHTTPRequestHandler):
    def _send(self,b):
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(page(b))
    def do_GET(self):
        p=self.path.split('?')[0]
        if p=='/': self._send(S['body'])
        elif p=='/declined': self._send('<h2>登録しませんでした</h2><p>またのご利用をお待ちしています。</p>')
        elif p=='/privacy': self._send('<h2>プライバシーポリシー</h2><p>登録情報はメルマガ配信のみに使い、第三者に提供しません。</p>')
        else: self.send_response(404);self.end_headers()
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0)); d=urllib.parse.parse_qs(self.rfile.read(n).decode(),keep_blank_values=True)
        rec={'site':name,'received_at':datetime.datetime.now().astimezone().isoformat(),'fields':{k:v[0] for k,v in d.items()}}
        open(LOG,'a').write(json.dumps(rec,ensure_ascii=False)+'\n')
        self._send(S['done'])
    def log_message(self,f,*a):
        open(f'{BASE}/{name}_access.log','a').write(datetime.datetime.now().astimezone().isoformat()+' '+(f%a)+'\n')
http.server.ThreadingHTTPServer(('127.0.0.1',S['port']),H).serve_forever()
