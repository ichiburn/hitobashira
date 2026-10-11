"""役割間の引き継ぎ（ファイル受け渡し）。標準ライブラリのみ。

状態の遷移（これ以外は ValueError）:
  (なし)   → CREATED   統括（coordinator）が許可範囲を固定して人柱Botへ
  CREATED  → SURVEYED  人柱Bot（hitobashira）→ 観測Bot
  SURVEYED → OBSERVED  観測Bot（observer）→ 検証官
  OBSERVED → VERIFIED  検証官（verifier）→ 統括。判定を固定した時刻とハッシュ（verify_lock）を必ず含む
  VERIFIED → REPORTED  統括（最後）
  どの状態からでも → ABORTED（理由つき。途中停止を隠さない）
受け手は accept() で、スキーマ・遷移・前の引き継ぎとのハッシュの連鎖・成果物のハッシュを確かめてから作業する。
"""
import json, os, re
from harness.runctx import sha256_file, now_iso

SCHEMA = "hitobashira.handoff/v1"
ROLES = ("coordinator", "hitobashira", "observer", "verifier")
TRANSITIONS = {  # (prev_state, state): (from_role, to_role)
    (None, "CREATED"): ("coordinator", "hitobashira"),
    ("CREATED", "SURVEYED"): ("hitobashira", "observer"),
    ("SURVEYED", "OBSERVED"): ("observer", "verifier"),
    ("OBSERVED", "VERIFIED"): ("verifier", "coordinator"),
    ("VERIFIED", "REPORTED"): ("coordinator", "coordinator"),
}
REQUIRED = ("schema", "run_id", "seq", "from_role", "to_role", "prev_state", "state", "created_at", "artifacts",
            "prev_handoff_sha256")
ALLOWED_KEYS = set(REQUIRED) | {"withheld", "scope", "verify_lock", "reason", "notes"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class HandoffError(ValueError):
    pass


def validate(h):
    """schemas/handoff.schema.json と同じ制約を確かめる。問題があれば HandoffError。"""
    if not isinstance(h, dict):
        raise HandoffError("オブジェクトではない")
    miss = [k for k in REQUIRED if k not in h]
    if miss:
        raise HandoffError(f"必須キーが無い: {miss}")
    extra = set(h) - ALLOWED_KEYS
    if extra:
        raise HandoffError(f"未知のキー: {sorted(extra)}")
    if h["schema"] != SCHEMA:
        raise HandoffError("schema が違う")
    if not isinstance(h["seq"], int) or h["seq"] < 0:
        raise HandoffError("seq が不正")
    if h["from_role"] not in ROLES or h["to_role"] not in ROLES:
        raise HandoffError("role が不正")
    if not isinstance(h["artifacts"], list):
        raise HandoffError("artifacts が配列ではない")
    for a in h["artifacts"]:
        if not isinstance(a, dict) or not isinstance(a.get("path"), str) or not HEX64.match(str(a.get("sha256"))):
            raise HandoffError(f"artifact が不正: {a}")
        if os.path.isabs(a["path"]) or ".." in a["path"].split("/"):
            raise HandoffError(f"artifact のパスは run ディレクトリ内の相対パスに限る: {a['path']}")
    if h["state"] == "ABORTED":
        if not h.get("reason"):
            raise HandoffError("ABORTED には reason が必要")
    else:
        t = TRANSITIONS.get((h["prev_state"], h["state"]))
        if not t:
            raise HandoffError(f"許可されていない遷移: {h['prev_state']} → {h['state']}")
        if (h["from_role"], h["to_role"]) != t:
            raise HandoffError(f"遷移 {h['prev_state']}→{h['state']} の担当は {t[0]}→{t[1]}")
    if h["state"] == "VERIFIED":
        vl = h.get("verify_lock")
        if not (isinstance(vl, dict) and vl.get("locked_at") and HEX64.match(str(vl.get("sha256")))):
            raise HandoffError("VERIFIED には verify_lock（固定時刻とハッシュ）が必要")
    return True


class Chain:
    """1つの run の引き継ぎの連鎖。handoff/NN_<from>_to_<to>.json に新規作成で書く。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.dir = os.path.join(ctx.dir, "handoff")
        os.makedirs(self.dir, exist_ok=True)
        self.items = []  # (path, dict)

    @property
    def state(self):
        return self.items[-1][1]["state"] if self.items else None

    def artifact(self, path):
        kind = os.path.relpath(path, self.ctx.dir).split(os.sep)[0]
        return {"path": os.path.relpath(path, self.ctx.dir).replace(os.sep, "/"), "sha256": sha256_file(path),
                "kind": kind if kind in ("evidence", "derived", "verdict", "handoff") else "derived"}

    def emit(self, from_role, to_role, state, artifacts=(), **extra):
        prev_path = self.items[-1][0] if self.items else None
        h = {"schema": SCHEMA, "run_id": self.ctx.run_id, "seq": len(self.items), "from_role": from_role,
             "to_role": to_role, "prev_state": self.state, "state": state, "created_at": now_iso(),
             "artifacts": [self.artifact(p) for p in artifacts],
             "prev_handoff_sha256": sha256_file(prev_path) if prev_path else None}
        h.update({k: v for k, v in extra.items() if v is not None})
        validate(h)
        p = os.path.join(self.dir, f"{len(self.items):02d}_{from_role}_to_{to_role}.json")
        self.ctx._write_once(p, (json.dumps(h, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.items.append((p, h))
        self.ctx.log("handoff", path=self.ctx.rel(p), sha256=sha256_file(p), from_role=from_role, to_role=to_role,
                     state=state)
        return p, h

    def accept(self, role):
        """受け手 role が最新の引き継ぎを受け取る。ハッシュの連鎖と成果物を確かめ、成果物のパス一覧を返す。"""
        if not self.items:
            raise HandoffError("引き継ぎが無い")
        p, h = self.items[-1]
        with open(p, encoding="utf-8") as f:
            disk = json.load(f)
        validate(disk)
        if disk != h:
            raise HandoffError("引き継ぎファイルが書き換えられている")
        if disk["to_role"] != role:
            raise HandoffError(f"宛先が {disk['to_role']}（{role} ではない）")
        if len(self.items) > 1 and disk["prev_handoff_sha256"] != sha256_file(self.items[-2][0]):
            raise HandoffError("前の引き継ぎとのハッシュがつながらない")
        out = []
        for a in disk["artifacts"]:
            ap = os.path.join(self.ctx.dir, a["path"])
            if sha256_file(ap) != a["sha256"]:
                raise HandoffError(f"成果物のハッシュが違う: {a['path']}")
            out.append(ap)
        self.ctx.log("handoff_accepted", role=role, path=self.ctx.rel(p), artifacts=len(out))
        return out, disk
