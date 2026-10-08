"""Step 18 §22: 3 contacts with contrasting Gold, same message, verify adaptation."""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "backend")
from fastapi.testclient import TestClient
from app import config, database
from app.main import app
from app.ai import factory
from app.learning import style
from app.routers import generation
from api_key_file import read_gemini_api_key
from benchmark_config import build_gemini_benchmark_config
from benchmark_response import benchmark_run_state, extract_api_error_code


CONTACTS = {
    # A: short/tame/laugh-heavy
    "A": [
        ("今週ずっと忙しくて疲れた", "おつかれ笑"),
        ("昨日映画見てきた", "映画いいね笑"),
        ("カフェ行ってきたよ", "いいな笑"),
        ("コンビニで新作スイーツ見つけた", "新作気になる笑"),
        ("天気いいね", "ほんとだね笑"),
        ("週末はゆっくりできそう", "いいじゃん笑"),
    ],
    # B: polite/long/no-emoji
    "B": [
        ("今週ずっと忙しくて疲れた", "お仕事お疲れ様です！ゆっくり休んでくださいね。"),
        ("昨日映画見てきた", "映画いいですね！どんな作品を見たんですか？"),
        ("カフェ行ってきたよ", "カフェに行かれたんですね！いい時間を過ごせましたか？"),
        ("コンビニで新作スイーツ見つけた", "新作スイーツ気になりますね！美味しそうですね。"),
        ("天気いいね", "今日は過ごしやすい天気ですね！"),
        ("週末はゆっくりできそう", "ゆっくり過ごせそうでよかったです！何か予定はありますか？"),
    ],
    # C: medium length, with polite and casual replies mixed
    "C": [
        ("今週ずっと忙しくて疲れた", "それは大変だったね、今日はゆっくり休んでね"),
        ("昨日映画見てきた", "映画いいですね、楽しそう！"),
        ("カフェ行ってきたよ", "カフェいいな、ゆっくりできそう"),
        ("コンビニで新作スイーツ見つけた", "それ気になる！どんな味だろう"),
        ("天気いいね", "ほんとだね、気持ちよさそう！"),
        ("週末はゆっくりできそう", "よかったですね！ゆっくりできそう"),
    ],
}

PROBE = "仕事で疲れた"


def seed_and_generate(client, key, model, delay_seconds, probe, secondary_key=""):
    ai_config = build_gemini_benchmark_config(
        primary_key=key, secondary_key=secondary_key, model=model
    )
    generation.get_ai_config = lambda: ai_config
    contact_ids = {}
    for name, pairs in CONTACTS.items():
        cid = client.post("/api/contacts", json={"name": f"{name}さん", "profile": ""}).json()["id"]
        for contact_message, self_message in pairs:
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_message})
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": self_message})
        contact_ids[name] = cid

    out = {}
    for name in CONTACTS:
        cid = contact_ids[name]
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": probe})
        r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
        if r.status_code != 200:
            out[name] = {
                "error": f"HTTP {r.status_code}",
                "error_code": extract_api_error_code(r),
            }
            break
        else:
            data = r.json()
            history_ids = data.get("history_ids", [])
            conn = database.get_conn()
            try:
                models = [row["model"] for row in conn.execute(
                    f"SELECT DISTINCT model FROM generation_history WHERE id IN ({','.join('?' for _ in history_ids)})",
                    history_ids,
                ).fetchall()] if history_ids else []
            finally:
                conn.close()
            profile = style.compute_hierarchical_profile(cid)
            learned = profile["active_profile"]
            out[name] = {
                "replies": data["replies"],
                "models_used": models,
                "style_profile": {
                    "tier": profile["hierarchy_tier"],
                    "gold_samples": profile["same_contact_gold_samples"],
                    "adaptation_weight": profile["contact_adaptation_weight"],
                    "length_median": learned.char_median,
                    "keigo_ratio": learned.keigo_ratio,
                    "hybrid_ratio": learned.hybrid_ratio,
                    "tame_ratio": learned.tame_ratio,
                    "laugh_ratio": learned.laugh_ratio,
                },
            }
        time.sleep(max(0.0, delay_seconds))
    return out


def style_sig(text):
    tame = 1 if re.search(r"(笑|w|だね|だよ|よね|じゃん)", text) else 0
    keigo = 1 if re.search(r"(です|ます|でした|ました)", text) else 0
    return {"len": len(text), "tame": tame, "keigo": keigo,
            "laugh": 1 if ("笑" in text or "w" in text) else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"),
                    help="API key file (value is never printed)")
    ap.add_argument("--secondary-env-file", default="",
                    help="Optional second-account key file (value is never printed)")
    ap.add_argument("--db", help="Optional new/empty database path; existing files are never removed")
    ap.add_argument("--delay-seconds", type=float, default=6.0)
    ap.add_argument("--probe", default=PROBE, help="Shared incoming message used for A/B/C")
    args = ap.parse_args()
    key = read_gemini_api_key(Path(args.env_file))
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    secondary_key = (
        read_gemini_api_key(Path(args.secondary_env_file))
        if args.secondary_env_file
        else ""
    )
    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="contactbench_")) / "contactbench.db"
    if db_path.exists() and db_path.stat().st_size:
        print(f"Refusing to overwrite non-empty benchmark database: {db_path}")
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config.DB_PATH = db_path
    database.init_db()
    client = TestClient(app)
    out = seed_and_generate(
        client, key, args.model, args.delay_seconds, args.probe, secondary_key
    )
    # discrimination: each reply closer to own Gold than to others?
    gold_sig = {n: [style_sig(sm) for _, sm in pairs] for n, pairs in CONTACTS.items()}
    report = {}
    for name, res in out.items():
        if "error" in res:
            report[name] = res
            continue
        entry = {
            "replies": res["replies"],
            "sigs": [style_sig(c) for c in res["replies"]],
            "style_profile": res["style_profile"],
        }
        # avg laugh/len vs own Gold avg laugh/len
        own = gold_sig[name]
        own_laugh = sum(g["laugh"] for g in own) / len(own)
        own_len = sum(g["len"] for g in own) / len(own)
        rep_laugh = sum(s["laugh"] for s in entry["sigs"]) / len(entry["sigs"])
        rep_len = sum(s["len"] for s in entry["sigs"]) / len(entry["sigs"])
        entry["own_gold"] = {"laugh": round(own_laugh, 2), "len": round(own_len, 1)}
        entry["reply_avg"] = {"laugh": round(rep_laugh, 2), "len": round(rep_len, 1)}
        report[name] = entry
    report["probe"] = args.probe
    case_results = [report[name] for name in CONTACTS if name in report]
    run_state = benchmark_run_state(case_results, len(CONTACTS))
    report["run_status"] = {
        **run_state,
        "completed_contacts": len(case_results),
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {args.out}")
    print(json.dumps({n: {"own": r.get("own_gold"), "rep": r.get("reply_avg"),
                          "replies": r.get("replies")} for n, r in report.items()
                      if n in CONTACTS and isinstance(r, dict)},
                     ensure_ascii=False, indent=1)[:2000])
    return 0 if run_state["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
