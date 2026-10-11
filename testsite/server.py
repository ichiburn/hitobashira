# 注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。
# 待受アドレスは 127.0.0.1（ループバック）のみ。
import http.server, json, urllib.parse, datetime
import os
LOG=os.path.join(os.path.dirname(os.path.abspath(__file__)),'submissions.jsonl')
class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0)); d=urllib.parse.parse_qs(self.rfile.read(n).decode())
        rec={'received_at':datetime.datetime.now().astimezone().isoformat(),'fields':{k:v[0] for k,v in d.items()}}
        open(LOG,'a').write(json.dumps(rec,ensure_ascii=False)+'\n')
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
        self.wfile.write('<html><body><div style="background:#222;color:#ff0;padding:6px">DEMO テストサイト：自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。</div><h2>ご登録ありがとうございました！</h2><p>確認メールをお送りしました。</p></body></html>'.encode())
http.server.ThreadingHTTPServer(('127.0.0.1',8765),H).serve_forever()
