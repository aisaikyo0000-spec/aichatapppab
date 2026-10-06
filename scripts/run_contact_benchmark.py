"""Step 18 §22: 3 contacts with contrasting Gold, same message, verify adaptation."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "backend")
from fastapi.testclient import TestClient
from app import config, database
from app.main import app
from app.ai import factory
from app.routers import generation


def _read_key() -> str:
    m = re.search(r"GEMINI_API_KEY=(\S+)", Path(".env").read_text(encoding="utf-8"))
    return m.group(1) if m else ""


CONTACTS = {
    # A: short/tame/laugh-heavy
    "A": [
        ("今日暇だった", "おつかれ笑"),
        ("眠い", "わかる笑"),
        ("雨降ってきた", "ほんとそれ笑"),
        ("おつ", "おつおつ笑"),
        ("まじ", "まじか笑"),
    ],
    # B: polite/long/no-emoji
    "B": [
        ("今日はありがとうございました", "こちらこそありがとうございました！とても楽しかったです！"),
        ("明日はよろしくお願いします", "こちらこそよろしくお願いいたします！準備を進めておきます！"),
        ("資料を送付しました", "資料を確認いたしました！ありがとうございます！"),
        ("会議は来週です", "承知いたしました！来週よろしくお願いいたします！"),
        ("お疲れ様でした", "お疲れ様でした！本日はありがとうございました！"),
    ],
    # C: medium/hybrid
    "C": [
        ("昨日映画見てきた", "映画いいですね！何を見たんですか？"),
        ("明日休みなんだ", "お休みなんですね！ゆっくり休んでくださいね。"),
        ("コンビニで新作スイーツ見つけた", "新作気になりますね！美味しそうです。"),
        ("来週引っ越しするんだ", "引っ越しなんですね！大変そうですね。"),
        ("最近ランニング始めた", "ランニングいいですね！すごいです。"),
    ],
}

PROBE = "今日疲れた"


def seed_and_generate(client, key, model):
    generation.get_ai_config = lambda: {
        "provider": "gemini", "model": model, "api_key": key,
        "temperature": 0.8, "max_tokens": 512, "history_limit": 50,
    }
    out = {}
    for name, pairs in CONTACTS.items():
        cid = client.post("/api/contacts", json={"name": f"{name}さん", "profile": ""}).json()["id"]
        for cm, sm in pairs:
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": cm})
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": sm})
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": PROBE})
        r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
        if r.status_code != 200:
            out[name] = {"error": f"HTTP {r.status_code}"}
        else:
            out[name] = {"replies": r.json()["replies"]}
        time.sleep(2)
    return out


def style_sig(text):
    tame = 1 if re.search(r"(笑|w|だね|だよ|よね|じゃん)", text) else 0
    keigo = 1 if re.search(r"(です|ます|でした|ました)", text) else 0
    return {"len": len(text), "tame": tame, "keigo": keigo,
            "laugh": 1 if ("笑" in text or "w" in text) else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--db", default="C:/Users/proje/AppData/Local/Temp/opencode/contactbench.db")
    args = ap.parse_args()
    key = _read_key()
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    Path(args.db).unlink(missing_ok=True)
    config.DB_PATH = args.db
    database.init_db()
    client = TestClient(app)
    out = seed_and_generate(client, key, args.model)
    # discrimination: each reply closer to own Gold than to others?
    gold_sig = {n: [style_sig(sm) for _, sm in pairs] for n, pairs in CONTACTS.items()}
    report = {}
    for name, res in out.items():
        if "error" in res:
            report[name] = res
            continue
        entry = {"replies": res["replies"], "sigs": [style_sig(c) for c in res["replies"]]}
        # avg laugh/len vs own Gold avg laugh/len
        own = gold_sig[name]
        own_laugh = sum(g["laugh"] for g in own) / len(own)
        own_len = sum(g["len"] for g in own) / len(own)
        rep_laugh = sum(s["laugh"] for s in entry["sigs"]) / len(entry["sigs"])
        rep_len = sum(s["len"] for s in entry["sigs"]) / len(entry["sigs"])
        entry["own_gold"] = {"laugh": round(own_laugh, 2), "len": round(own_len, 1)}
        entry["reply_avg"] = {"laugh": round(rep_laugh, 2), "len": round(rep_len, 1)}
        report[name] = entry
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {args.out}")
    print(json.dumps({n: {"own": r.get("own_gold"), "rep": r.get("reply_avg"),
                          "replies": r.get("replies")} for n, r in report.items()},
                     ensure_ascii=False, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
