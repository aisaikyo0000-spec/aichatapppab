import csv
import json

import pytest

from scripts.reply_quality_annotations import (
    DIMENSIONS,
    compare_rating_files,
    export_blinded_csv,
    quadratic_weighted_kappa,
)


def _artifact(path):
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": f"secret-case-{i}",
                        "contact": f"相手の発言{i}",
                        "candidates": [f"返信{i}-a", f"返信{i}-b"],
                        "models_used": ["secret-model"],
                        "trace": {
                            "input": {"text": f"相手の発言{i}", "intent": "report"},
                            "provider": "secret-provider",
                            "account": "secret-account",
                            "retrieved_pairs": [{"pair_id": "secret-pair", "score": 9}],
                        },
                        "naturalness_scores": [{"score": 0.1}],
                    }
                    for i in range(8)
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_export_is_blinded_repeatable_and_seed_randomized(tmp_path):
    artifact = tmp_path / "run.json"
    _artifact(artifact)
    first, second, other_seed = (tmp_path / name for name in ("a.csv", "b.csv", "c.csv"))

    export_blinded_csv(artifact, first, seed=14)
    export_blinded_csv(artifact, second, seed=14)
    export_blinded_csv(artifact, other_seed, seed=15)

    def rows(path):
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    a, b, c = rows(first), rows(second), rows(other_seed)
    assert a == b
    assert [row["item_id"] for row in a] != [row["item_id"] for row in c]
    assert {row["item_id"] for row in a} == {row["item_id"] for row in c}
    assert len(a) == 16
    assert set(a[0]) == {
        "item_id",
        "counterpart_message",
        "conversation_context",
        "reply",
        *DIMENSIONS,
        "echo_classification",
        "reason",
    }
    rendered = first.read_text(encoding="utf-8-sig")
    for secret in ("secret-case", "secret-model", "secret-provider", "secret-account", "secret-pair", "score"):
        assert secret not in rendered
    assert "返信0-a" in rendered


def test_export_reads_context_when_present_without_exposing_other_metadata(tmp_path):
    artifact = tmp_path / "context.json"
    artifact.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "private-id",
                        "contact": "いま帰ったよ",
                        "context": ["私: おつかれ！"],
                        "candidates": ["ゆっくり休んでね"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "ratings.csv"

    export_blinded_csv(artifact, output, seed=1)

    with output.open(encoding="utf-8-sig", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["counterpart_message"] == "いま帰ったよ"
    assert "おつかれ" in row["conversation_context"]
    assert row["reply"] == "ゆっくり休んでね"


def test_quadratic_weighted_kappa_has_expected_ordinal_value():
    assert quadratic_weighted_kappa([1, 2, 3, 4, 5], [1, 2, 4, 4, 5]) == pytest.approx(20 / 21)
    assert quadratic_weighted_kappa([3, 3, 3], [3, 3, 3]) == 1.0
    assert quadratic_weighted_kappa([1, 1], [5, 5]) == 0.0


def test_compare_requires_same_items_and_complete_valid_dimensions(tmp_path):
    left, right = tmp_path / "left.csv", tmp_path / "right.csv"
    fields = ["item_id", *DIMENSIONS, "echo_classification", "reason"]
    row = {"item_id": "opaque-1", "echo_classification": "natural_topic_reuse", "reason": ""}
    for dimension in DIMENSIONS:
        row[dimension] = "4"
    for path, item in ((left, row), (right, {**row, "context_fit": "3"})):
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerow(item)

    result = compare_rating_files(left, right)
    assert result["item_count"] == 1
    assert result["dimensions"]["context_fit"]["exact_agreement"] == 0.0
    assert result["dimensions"]["context_fit"]["quadratic_weighted_kappa"] == 0.0
    assert result["dimensions"]["echo_classification"]["exact_agreement"] == 1.0

    row["necessary_information"] = ""
    with left.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError, match="missing or invalid"):
        compare_rating_files(left, right)

    row["necessary_information"] = "4"
    with left.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
    with right.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow({**row, "item_id": "different-item", "necessary_information": "2"})
    with pytest.raises(ValueError, match="item IDs do not match"):
        compare_rating_files(left, right)


def test_agreement_rejects_out_of_range_ratings_and_missing_boundary_reason(tmp_path):
    path = tmp_path / "ratings.csv"
    fields = ["item_id", *DIMENSIONS, "echo_classification", "reason"]
    row = {"item_id": "opaque", "echo_classification": "simple_repetition", "reason": ""}
    for dimension in DIMENSIONS:
        row[dimension] = "4"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow({**row, "context_fit": "6"})
    with pytest.raises(ValueError, match="missing or invalid"):
        compare_rating_files(path, path)

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow({**row, "factual_grounding": "1"})
    with pytest.raises(ValueError, match="reason is required"):
        compare_rating_files(path, path)


def test_agreement_requires_a_valid_echo_classification(tmp_path):
    path = tmp_path / "ratings.csv"
    fields = ["item_id", *DIMENSIONS, "echo_classification", "reason"]
    row = {"item_id": "opaque", "echo_classification": "", "reason": ""}
    row.update({dimension: "3" for dimension in DIMENSIONS})
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(ValueError, match="missing or invalid echo classification"):
        compare_rating_files(path, path)


def test_export_neutralizes_spreadsheet_formula_text(tmp_path):
    artifact = tmp_path / "formula.json"
    artifact.write_text(
        json.dumps({"cases": [{"contact": "=1+1", "candidates": ["@SUM(A1:A2)"]}]}),
        encoding="utf-8",
    )
    output = tmp_path / "ratings.csv"
    export_blinded_csv(artifact, output, seed=1)
    with output.open(encoding="utf-8-sig", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["counterpart_message"] == "'=1+1"
    assert row["reply"] == "'@SUM(A1:A2)"
