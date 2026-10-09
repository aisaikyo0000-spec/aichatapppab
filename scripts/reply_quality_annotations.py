"""Offline helpers for blinded human review of reply-quality benchmark artifacts.

This tool never calls a model or opens a network connection. Exported CSV files
may contain private conversations; keep the artifact and ratings local.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Iterable, Sequence


DIMENSIONS = (
    "context_fit",
    "necessary_information",
    "factual_grounding",
    "conversational_naturalness",
    "sendability",
)
EXPORT_FIELDS = (
    "item_id",
    "counterpart_message",
    "conversation_context",
    "reply",
    *DIMENSIONS,
    "echo_classification",
    "reason",
)
ECHO_CLASSIFICATIONS = (
    "simple_repetition",
    "simple_paraphrase",
    "natural_topic_reuse",
    "off_context_new_topic",
)
_CONTEXT_FIELDS = ("conversation_context", "context", "conversation_history", "history", "conversation")


def _text(value: object) -> str:
    """Render only explicitly supplied conversational text, never metadata."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _text(item)))
    if isinstance(value, dict):
        # Context records may be {sender, content} or {role, text}; do not dump
        # arbitrary objects such as scores, IDs, or provider/model metadata.
        speaker = value.get("sender", value.get("role", ""))
        content = value.get("content", value.get("text", value.get("message", "")))
        body = _text(content)
        return f"{speaker}: {body}" if speaker and body else body
    return ""


def _case_message(case: dict) -> str:
    trace_input = case.get("trace", {}).get("input", {}) if isinstance(case.get("trace"), dict) else {}
    value = case.get("contact") or case.get("counterpart_message")
    if not value and isinstance(trace_input, dict):
        value = trace_input.get("text", "")
    return _text(value)


def _case_context(case: dict) -> str:
    for field in _CONTEXT_FIELDS:
        if case.get(field):
            return _text(case[field])
    # Some artifact producers place a small context object under input. Keep
    # this limited to conversation fields; do not surface trace/model prompts.
    input_value = case.get("input")
    if isinstance(input_value, dict):
        for field in _CONTEXT_FIELDS:
            if input_value.get(field):
                return _text(input_value[field])
    return ""


def _candidate_text(candidate: object) -> str:
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        for field in ("reply", "text", "content"):
            if isinstance(candidate.get(field), str):
                return candidate[field]
    return ""


def _spreadsheet_safe(value: str) -> str:
    """Keep untrusted chat text from being evaluated as a CSV spreadsheet formula."""
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _item_id(message: str, context: str, reply: str, duplicate_ordinal: int) -> str:
    # Hash only user-visible evaluation content. A duplicate ordinal distinguishes
    # identical candidate rows without embedding a source case ID or candidate index.
    payload = json.dumps(
        [message, context, reply, duplicate_ordinal],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "rqa-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def export_blinded_csv(artifact_path: str | Path, output_path: str | Path, *, seed: int) -> int:
    """Export candidate replies and only conversational context to a blinded CSV."""
    artifact_bytes = Path(artifact_path).read_bytes()
    artifact = json.loads(artifact_bytes.decode("utf-8"))
    cases = artifact.get("cases") if isinstance(artifact, dict) else artifact
    if not isinstance(cases, list):
        raise ValueError("artifact must contain a cases list (or be a list of cases)")

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    duplicate_counts: dict[tuple[str, str, str], int] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        message = _case_message(case)
        context = _case_context(case)
        candidates = case.get("candidates", case.get("replies", []))
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            reply = _candidate_text(candidate)
            if not reply.strip():
                continue
            item_key = (message, context, reply)
            duplicate_ordinal = duplicate_counts.get(item_key, 0)
            duplicate_counts[item_key] = duplicate_ordinal + 1
            item_id = _item_id(message, context, reply, duplicate_ordinal)
            if item_id in seen:
                raise ValueError("duplicate blinded item ID; ensure case IDs are unique")
            seen.add(item_id)
            rows.append({
                "item_id": item_id,
                "counterpart_message": _spreadsheet_safe(message),
                "conversation_context": _spreadsheet_safe(context),
                "reply": _spreadsheet_safe(reply),
                **{dimension: "" for dimension in DIMENSIONS},
                "echo_classification": "",
                "reason": "",
            })
    if not rows:
        raise ValueError("artifact contains no candidate replies to annotate")

    random.Random(seed).shuffle(rows)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _read_ratings(path: str | Path) -> dict[str, dict[str, int | str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"item_id", *DIMENSIONS, "echo_classification"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path}: missing required item_id/rubric columns")
        ratings: dict[str, dict[str, int | str]] = {}
        for line, row in enumerate(reader, start=2):
            item_id = (row.get("item_id") or "").strip()
            if not item_id:
                raise ValueError(f"{path}:{line}: missing item_id")
            if item_id in ratings:
                raise ValueError(f"{path}:{line}: duplicate item_id {item_id}")
            values: dict[str, int | str] = {}
            for dimension in DIMENSIONS:
                raw = (row.get(dimension) or "").strip()
                try:
                    value = int(raw)
                except ValueError as exc:
                    raise ValueError(f"{path}:{line}: missing or invalid rating for {dimension}") from exc
                if str(value) != raw or not 1 <= value <= 5:
                    raise ValueError(f"{path}:{line}: missing or invalid rating for {dimension}")
                values[dimension] = value
            echo_classification = (row.get("echo_classification") or "").strip()
            if echo_classification not in ECHO_CLASSIFICATIONS:
                raise ValueError(f"{path}:{line}: missing or invalid echo classification")
            if any(value in (1, 5) for value in values.values()) and not (row.get("reason") or "").strip():
                raise ValueError(f"{path}:{line}: reason is required for ratings of 1 or 5")
            values["echo_classification"] = echo_classification
            ratings[item_id] = values
    if not ratings:
        raise ValueError(f"{path}: no ratings found")
    return ratings


def quadratic_weighted_kappa(left: Sequence[int], right: Sequence[int]) -> float | None:
    """Return quadratic weighted Cohen kappa for paired 1–5 ordinal ratings."""
    if len(left) != len(right) or not left:
        raise ValueError("rating sequences must have the same non-zero length")
    if any(not isinstance(value, int) or not 1 <= value <= 5 for value in (*left, *right)):
        raise ValueError("ratings must be integers from 1 to 5")
    n = len(left)
    observed_disagreement = sum(((a - b) / 4) ** 2 for a, b in zip(left, right)) / n
    left_counts = [left.count(value) for value in range(1, 6)]
    right_counts = [right.count(value) for value in range(1, 6)]
    expected_disagreement = sum(
        left_counts[a - 1] * right_counts[b - 1] * ((a - b) / 4) ** 2
        for a in range(1, 6)
        for b in range(1, 6)
    ) / (n * n)
    if expected_disagreement == 0:
        return 1.0 if observed_disagreement == 0 else None
    return 1.0 - observed_disagreement / expected_disagreement


def compare_rating_files(left_path: str | Path, right_path: str | Path) -> dict:
    left, right = _read_ratings(left_path), _read_ratings(right_path)
    if left.keys() != right.keys():
        missing_right = sorted(left.keys() - right.keys())
        missing_left = sorted(right.keys() - left.keys())
        raise ValueError(
            "item IDs do not match: "
            f"missing from second={len(missing_right)}, missing from first={len(missing_left)}"
        )
    ids = sorted(left)
    results = {}
    for dimension in DIMENSIONS:
        a = [int(left[item][dimension]) for item in ids]
        b = [int(right[item][dimension]) for item in ids]
        results[dimension] = {
            "exact_agreement": sum(x == y for x, y in zip(a, b)) / len(ids),
            "quadratic_weighted_kappa": quadratic_weighted_kappa(a, b),
        }
    results["echo_classification"] = {
        "exact_agreement": sum(
            left[item]["echo_classification"] == right[item]["echo_classification"]
            for item in ids
        ) / len(ids)
    }
    return {"item_count": len(ids), "dimensions": results}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline blinded human review of reply-quality artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser("export", help="export blinded candidate replies to CSV")
    export_parser.add_argument("artifact", type=Path)
    export_parser.add_argument("output", type=Path)
    export_parser.add_argument("--seed", type=int, required=True, help="seed used to randomize row order")
    agreement_parser = subparsers.add_parser("agreement", help="compare two completed rating CSV files")
    agreement_parser.add_argument("first", type=Path)
    agreement_parser.add_argument("second", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    print("LOCAL/PRIVACY WARNING: files can contain private chats. Keep artifacts and rating CSVs local. This tool makes no API or network calls.", file=sys.stderr)
    try:
        if args.command == "export":
            count = export_blinded_csv(args.artifact, args.output, seed=args.seed)
            print(f"Exported {count} blinded replies to {args.output}")
        else:
            print(json.dumps(compare_rating_files(args.first, args.second), ensure_ascii=False, indent=2))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
