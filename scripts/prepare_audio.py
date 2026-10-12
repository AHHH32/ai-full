"""Export the text manifest for the optional Windows offline voice builder."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/server"))
from app.story import REPLIES, audio_key, sentences

lines = {audio_key(line): line for text in REPLIES.values() for line in sentences(text)}
target = ROOT / "tmp/mock-lines.json"
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
print(f"Prepared {len(lines)} spoken sentences")
