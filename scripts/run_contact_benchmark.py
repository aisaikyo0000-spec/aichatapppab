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
from benchmark_config import (
    add_active_account_argument,
    build_gemini_benchmark_config,
    load_gemini_benchmark_route,
    record_gemini_benchmark_success,
    successful_gemini_benchmark_route,
)
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
MIN_GOLD_PAIRS_PER_CONTACT = 6
MAX_GOLD_PAIRS_PER_CONTACT = 12
MAX_GOLD_MESSAGE_CHARS = 2000
MAX_GOLD_FIXTURE_BYTES = 256_000


def contact_quality_status(*, generation_complete: bool, expected_replies: int) -> dict:
    """Keep generation completion separate from the required human quality judgment."""
    return {
        "status": "manual_review_required" if generation_complete else "generation_incomplete",
        "adaptation_pass": False,
        "expected_replies": expected_replies,
    }


def load_contact_fixture(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Load an external, anonymous A/B/C gold fixture without copying it into artifacts."""
    try:
        fixture_size = path.stat().st_size
    except OSError as exc:
        raise ValueError("fixture file could not be read as JSON") from exc
    if fixture_size > MAX_GOLD_FIXTURE_BYTES:
        raise ValueError("fixture file exceeds 256000 bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixture file could not be read as JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("fixture must be a JSON object")
    raw_contacts = payload.get("contacts")
    if not isinstance(raw_contacts, dict) or set(raw_contacts) != {"A", "B", "C"}:
        raise ValueError("fixture contacts must use anonymous labels A, B, C")

    contacts: dict[str, list[tuple[str, str]]] = {}
    for label in ("A", "B", "C"):
        raw_pairs = raw_contacts[label]
        if not isinstance(raw_pairs, list) or len(raw_pairs) < MIN_GOLD_PAIRS_PER_CONTACT:
            raise ValueError(f"contact {label} needs at least six gold pairs")
        if len(raw_pairs) > MAX_GOLD_PAIRS_PER_CONTACT:
            raise ValueError(f"contact {label} accepts at most 12 gold pairs")
        pairs: list[tuple[str, str]] = []
        for pair in raw_pairs:
            if not isinstance(pair, dict):
                raise ValueError(f"contact {label} pairs must be objects")
            incoming = pair.get("incoming")
            gold = pair.get("gold")
            if not isinstance(incoming, str) or not incoming.strip():
                raise ValueError(f"contact {label} has an empty incoming message")
            if not isinstance(gold, str) or not gold.strip():
                raise ValueError(f"contact {label} has an empty gold reply")
            if len(incoming) > MAX_GOLD_MESSAGE_CHARS or len(gold) > MAX_GOLD_MESSAGE_CHARS:
                raise ValueError(f"contact {label} contains a message over 2000 characters")
            pairs.append((incoming.strip(), gold.strip()))
        contacts[label] = pairs
    return contacts


def seed_and_generate(
    client,
    key,
    model,
    delay_seconds,
    probe,
    secondary_key="",
    route_state_path=None,
    active_account="primary",
    contacts=None,
):
    contact_fixtures = CONTACTS if contacts is None else contacts
    ai_config = build_gemini_benchmark_config(
        primary_key=key,
        secondary_key=secondary_key,
        model=model,
        active_account=active_account,
    )
    if route_state_path is not None:
        load_gemini_benchmark_route(ai_config, route_state_path)
    generation.get_ai_config = lambda: ai_config
    successful_attempts = []
    real_get_provider = factory.get_provider

    def tracking_get_provider(provider_name, api_key):
        provider = real_get_provider(provider_name, api_key)
        original_generate = provider.generate

        def tracking_generate(**kwargs):
            response = original_generate(**kwargs)
            successful_attempts.append((api_key, kwargs["model"]))
            return response

        provider.generate = tracking_generate
        return provider

    factory.get_provider = tracking_get_provider
    generation.factory.get_provider = tracking_get_provider
    contact_ids = {}
    for name, pairs in contact_fixtures.items():
        cid = client.post("/api/contacts", json={"name": f"{name}さん", "profile": ""}).json()["id"]
        for contact_message, self_message in pairs:
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_message})
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": self_message})
        contact_ids[name] = cid

    out = {}
    for name in contact_fixtures:
        cid = contact_ids[name]
        client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": probe})
        successful_route = None
        successful_before = len(successful_attempts)
        r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
        if r.status_code != 200:
            out[name] = {
                "error": f"HTTP {r.status_code}",
                "error_code": extract_api_error_code(r),
            }
            break
        else:
            if len(successful_attempts) > successful_before:
                successful_key, successful_model = successful_attempts[-1]
                successful_route = successful_gemini_benchmark_route(
                    ai_config,
                    api_key=successful_key,
                    model=successful_model,
                )
                record_gemini_benchmark_success(
                    ai_config,
                    api_key=successful_key,
                    model=successful_model,
                    route_state_path=route_state_path,
                )
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
                "successful_route": successful_route,
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
    add_active_account_argument(ap)
    ap.add_argument("--quota-route-state", type=Path,
                    help="Optional run-local state shared across benchmark stages")
    ap.add_argument("--db", help="Optional new/empty database path; existing files are never removed")
    ap.add_argument("--delay-seconds", type=float, default=6.0)
    ap.add_argument("--probe", default=PROBE, help="Shared incoming message used for A/B/C")
    ap.add_argument(
        "--gold-fixture",
        type=Path,
        help="Optional local JSON fixture with anonymous A/B/C gold pairs; input text is not copied to the artifact",
    )
    args = ap.parse_args()
    contact_fixtures = CONTACTS
    fixture_source = "synthetic_control"
    if args.gold_fixture:
        try:
            contact_fixtures = load_contact_fixture(args.gold_fixture)
        except ValueError as exc:
            print(f"Invalid --gold-fixture: {exc}")
            return 2
        fixture_source = "user_gold_fixture"
    if not args.probe.strip():
        print("Invalid --probe: message must not be empty")
        return 2
    if len(args.probe) > MAX_GOLD_MESSAGE_CHARS:
        print("Invalid --probe: message exceeds 2000 characters")
        return 2
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
        client,
        key,
        args.model,
        args.delay_seconds,
        args.probe,
        secondary_key,
        args.quota_route_state,
        args.active_account,
        contact_fixtures,
    )
    # discrimination: each reply closer to own Gold than to others?
    gold_sig = {n: [style_sig(sm) for _, sm in pairs] for n, pairs in contact_fixtures.items()}
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
    case_results = [report[name] for name in contact_fixtures if name in report]
    run_state = benchmark_run_state(case_results, len(contact_fixtures))
    report["run_status"] = {
        **run_state,
        "completed_contacts": len(case_results),
    }
    report["quality_review"] = contact_quality_status(
        generation_complete=run_state["complete"],
        expected_replies=len(contact_fixtures) * 3,
    )
    report["fixture_source"] = fixture_source
    report["gold_pairs_per_contact"] = {
        name: len(pairs) for name, pairs in contact_fixtures.items()
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {args.out}")
    print("Contact adaptation quality is not auto-scored; inspect every reply in the artifact.")
    print(json.dumps({n: {"own": r.get("own_gold"), "rep": r.get("reply_avg"),
                          "replies": r.get("replies")} for n, r in report.items()
                      if n in contact_fixtures and isinstance(r, dict)},
                     ensure_ascii=False, indent=1)[:2000])
    return 0 if run_state["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
