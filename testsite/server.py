# 注意: 自作の実験環境専用。所有する環境でのみ起動すること。実際の個人情報を入力しないこと。
# 待受アドレスは 127.0.0.1（ループバック）のみ。決まったルートだけを返す:
#   GET /        → index.html
#   POST /submit → 送信内容を submissions.jsonl に記録して完了画面
#   それ以外     → 404（GET/HEAD）/ 405（その他のメソッド）。ディレクトリ内のファイルは配信しない。
import http.server, json, urllib.parse, datetime, os

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, 'submissions.jsonl')
INDEX = os.path.join(BASE, 'index.html')
MAX_BODY = 64 * 1024
DONE = ('<html><body><div style="background:#222;color:#ff0;padding:6px">DEMO テストサイト：自作の実験環境専用。'
        '所有する環境でのみ起動すること。実際の個人情報を入力しないこと。</div>'
        '<h2>ご登録ありがとうございました！</h2><p>確認メールをお送りしました。</p></body></html>')


def build_record(body, client, user_agent, path='/submit'):
    """送信1件分の記録。空欄のフィールドも残し、同名フィールドが複数あれば配列で残す。"""
    d = urllib.parse.parse_qs(body, keep_blank_values=True)
    return {
        'received_at': datetime.datetime.now().astimezone().isoformat(),
        'path': path,
        'client': client,
        'user_agent': user_agent or '',
        'fields': {k: (v[0] if len(v) == 1 else v) for k, v in d.items()},
    }


class H(http.server.BaseHTTPRequestHandler):
    server_version = 'DemoTestsite'
    sys_version = ''

    def _send(self, code, body, ctype='text/html; charset=utf-8'):
        b = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(b)

    def _route(self):
        return urllib.parse.urlsplit(self.path).path

    def do_GET(self):
        if self._route() != '/':
            return self._send(404, 'not found', 'text/plain; charset=utf-8')
        with open(INDEX, encoding='utf-8') as f:
            self._send(200, f.read())

    do_HEAD = do_GET

    def do_POST(self):
        if self._route() != '/submit':
            return self._send(404, 'not found', 'text/plain; charset=utf-8')
        try:
            n = int(self.headers.get('Content-Length', 0))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return self._send(413 if n > MAX_BODY else 400, 'bad request', 'text/plain; charset=utf-8')
        body = self.rfile.read(n).decode('utf-8', 'replace')
        rec = build_record(body, f'{self.client_address[0]}:{self.client_address[1]}',
                           self.headers.get('User-Agent'), self._route())
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self._send(200, DONE)

    def _not_allowed(self):
        self._send(405, 'method not allowed', 'text/plain; charset=utf-8')

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed


if __name__ == '__main__':
    http.server.ThreadingHTTPServer(('127.0.0.1', 8765), H).serve_forever()
