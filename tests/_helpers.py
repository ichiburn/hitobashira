import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "tools"), os.path.join(ROOT, "testsite")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("HITOBASHIRA_BOT_LOCAL", "yourbot")
os.environ.setdefault("HITOBASHIRA_BOT_DOMAIN", "example.com")
