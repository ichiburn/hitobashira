"""実行ごとの run_id・出力ディレクトリ・追記専用ログ・SHA-256 マニフェスト（標準ライブラリのみ）。

出力構成（runs_local/<run_id>/）:
  evidence/   生の証拠。書き込みは新規作成のみ（既存ファイルは上書きしない）で、書いたら読み取り専用にする
  derived/    証拠から作った中間データ（台帳など。再生成できる）
  verdict/    判定（証拠とは別ファイル）
  log.jsonl   追記専用の実行ログ（許可判定・操作・証拠パス・判定時刻）
  manifest.json  evidence/ と verdict/ の SHA-256 一覧
SHA-256 が示せるのは「マニフェスト作成後に改変されていないか」の検知だけで、取得時の真正性は保証しない。
"""
import hashlib, json, os, secrets, stat
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))


def now_iso():
    return datetime.now(JST).isoformat(timespec="milliseconds")


def new_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


class RunContext:
    def __init__(self, base_dir, run_id=None):
        self.run_id = run_id or new_run_id()
        self.dir = os.path.join(os.path.abspath(base_dir), self.run_id)
        if os.path.exists(self.dir):
            raise FileExistsError(f"run ディレクトリが既にあります: {self.dir}")
        self.evidence_dir = os.path.join(self.dir, "evidence")
        self.verdict_dir = os.path.join(self.dir, "verdict")
        os.makedirs(self.evidence_dir)
        os.makedirs(self.verdict_dir)
        self.derived_dir = os.path.join(self.dir, "derived")
        os.makedirs(self.derived_dir)
        self.log_path = os.path.join(self.dir, "log.jsonl")
        self.log("run_start", run_id=self.run_id)

    def rel(self, path):
        return os.path.relpath(path, self.dir)

    def log(self, event, **kw):
        rec = {"at": now_iso(), "run_id": self.run_id, "event": event}
        rec.update(kw)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def _write_once(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as f:          # 既存ファイルがあれば FileExistsError（上書きしない）
            f.write(data)
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return path

    def put_evidence(self, name, data, role=None):
        """生の証拠を evidence/ に新規作成で書き、読み取り専用にする。"""
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if isinstance(data, str):
            data = data.encode("utf-8")
        p = self._write_once(os.path.join(self.evidence_dir, name), data)
        self.log("evidence_saved", path=self.rel(p), sha256=sha256_file(p), role=role)
        return p

    def put_derived(self, name, data, role=None):
        """証拠から作った中間データを derived/ に新規作成で書く。"""
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if isinstance(data, str):
            data = data.encode("utf-8")
        p = self._write_once(os.path.join(self.derived_dir, name), data)
        self.log("derived_saved", path=self.rel(p), sha256=sha256_file(p), role=role)
        return p

    def put_verdict(self, name, obj, role=None):
        """判定を verdict/ に新規作成で書く（証拠とは別ファイル）。判定時刻を記録する。obj が str なら Markdown などをそのまま書く。"""
        if isinstance(obj, str):
            p = self._write_once(os.path.join(self.verdict_dir, name), obj.encode("utf-8"))
            self.log("verdict_saved", path=self.rel(p), sha256=sha256_file(p), role=role)
            return p
        obj = dict(obj)
        obj.setdefault("decided_at", now_iso())
        data = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        p = self._write_once(os.path.join(self.verdict_dir, name), data)
        self.log("verdict_saved", path=self.rel(p), sha256=sha256_file(p), role=role, decided_at=obj["decided_at"])
        return p

    def write_manifest(self):
        files = []
        for sub in ("evidence", "derived", "verdict"):
            for root, _, names in os.walk(os.path.join(self.dir, sub)):
                for n in sorted(names):
                    p = os.path.join(root, n)
                    files.append({"path": self.rel(p), "sha256": sha256_file(p), "bytes": os.path.getsize(p)})
        files.sort(key=lambda x: x["path"])
        man = {"run_id": self.run_id, "created_at": now_iso(),
               "note": "SHA-256 は固定後の改変検知のためのもので、取得時の真正性は保証しない",
               "files": files}
        p = os.path.join(self.dir, "manifest.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=2)
            f.write("\n")
        self.log("manifest_written", path=self.rel(p), sha256=sha256_file(p), files=len(files))
        return p


def verify_manifest(run_dir):
    """manifest.json と現在のファイルを比べ、変わったものの一覧を返す（空なら一致）。"""
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as f:
        man = json.load(f)
    bad = []
    for e in man["files"]:
        p = os.path.join(run_dir, e["path"])
        if not os.path.exists(p) or sha256_file(p) != e["sha256"]:
            bad.append(e["path"])
    return bad
