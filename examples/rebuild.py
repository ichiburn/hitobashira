#!/usr/bin/env python3
"""examples/ を現行の tools/ で作り直す（標準ライブラリのみ。PNG は Pillow があれば）。

  python3 examples/rebuild.py          # sample_ledger.json と out/ を再生成
  python3 examples/rebuild.py --check  # 再生成結果がコミット済みの sample_ledger.json / out/*.md と一致するか確認

手順: sample_base.json（メール無しの台帳）に sample_mails/m*.json を ingest_email.py で順に取り込み、
report.py で報告書を作る。受信アドレスは例示用の yourbot@example.com に固定する。
"""
import glob, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
GENERATED_AT = "2026-10-11 16:30 JST（SAMPLE再生成）"


def build(dst_ledger, out_dir, png=True):
    env = dict(os.environ, HITOBASHIRA_BOT_LOCAL="yourbot", HITOBASHIRA_BOT_DOMAIN="example.com")
    shutil.copyfile(os.path.join(HERE, "sample_base.json"), dst_ledger)
    for m in sorted(glob.glob(os.path.join(HERE, "sample_mails", "m*.json"))):
        subprocess.run([sys.executable, os.path.join(TOOLS, "ingest_email.py"), m, "--ledger", dst_ledger],
                       check=True, env=env, stdout=subprocess.DEVNULL)
    args = [sys.executable, os.path.join(TOOLS, "report.py"), "--ledger", dst_ledger, "--out", out_dir,
            "--generated-at", GENERATED_AT] + ([] if png else ["--no-png"])
    r = subprocess.run(args, env=env, stdout=subprocess.DEVNULL)
    if r.returncode not in (0, 1):
        raise SystemExit(r.returncode)


def main():
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as t:
            build(os.path.join(t, "ledger.json"), os.path.join(t, "out"), png=False)
            ok = json.load(open(os.path.join(t, "ledger.json"), encoding="utf-8")) == \
                json.load(open(os.path.join(HERE, "sample_ledger.json"), encoding="utf-8"))
            for f in glob.glob(os.path.join(t, "out", "*.md")):
                ref = os.path.join(HERE, "out", os.path.basename(f))
                ok = ok and os.path.exists(ref) and open(f, encoding="utf-8").read() == open(ref, encoding="utf-8").read()
            print("一致" if ok else "不一致")
            return 0 if ok else 1
    out = os.path.join(HERE, "out")
    shutil.rmtree(out, ignore_errors=True)
    build(os.path.join(HERE, "sample_ledger.json"), out)
    print("再生成しました:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
