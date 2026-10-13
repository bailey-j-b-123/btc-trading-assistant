"""Read-only comparator probes; every source report comes from real offline runs."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from forward_endurance_harness import compare_reports, run_endurance


@pytest.fixture(scope="module")
def completed():
    result = run_endurance(30)
    result["phase"] = "complete"
    reports = []
    for case in result["cases"]:
        selected = dict(result, cases=[case])
        reports.append(selected)
    return result, reports


def cli():
    specification = importlib.util.spec_from_file_location("endurance_compare_cli", ROOT / "scripts" / "forward_endurance.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_saved_reports_reuse_the_identical_comparator(completed):
    actual, reports = completed
    comparison = compare_reports(*reports)
    assert comparison["exact_input_signature_verified"] is True
    for name in ("checks_passed", "canonical_agreement", "canonical_comparison", "outcome_chain_classification"):
        assert comparison[name] == actual[name]
    print("SAVED_COMPARISON=" + json.dumps({name: comparison[name] for name in ("checks_passed", "canonical_agreement", "canonical_comparison")}, sort_keys=True))


def test_saved_report_rejects_mismatched_actual_input(completed):
    _, reports = completed
    changed = deepcopy(reports)
    changed[1]["cases"][0]["input_signature"]["candles"] = "inert comparison-only wrong signature"
    with pytest.raises(ValueError, match="different candle/configuration"):
        compare_reports(*changed)


def test_legacy_metadata_is_explicitly_not_verified(completed):
    _, reports = completed
    legacy = deepcopy(reports)
    del legacy[0]["cases"][0]["input_signature"]
    result = compare_reports(*legacy)
    assert result["exact_input_signature_verified"] is False
    assert result["input_validation"].startswith("LEGACY")


def test_cli_comparison_preserves_full_evidence_and_exit_status(completed, tmp_path, monkeypatch, capsys):
    actual, reports = completed
    monkeypatch.chdir(tmp_path)
    for case, report in zip(("baseline", "stress"), reports):
        (tmp_path / f"{case}.json").write_text(json.dumps(report))
    inherited = dict(os.environ)
    try:
        if actual["checks_passed"]:
            cli().main(["--compare", "baseline.json", "stress.json", "--output", "comparison.json"])
        else:
            with pytest.raises(SystemExit) as exited:
                cli().main(["--compare", "baseline.json", "stress.json", "--output", "comparison.json"])
            assert exited.value.code == 1
    finally:
        os.environ.clear()
        os.environ.update(inherited)
    saved = json.loads((tmp_path / "comparison.json").read_text())
    assert saved["phase"] == "complete"
    assert saved["checks_passed"] == actual["checks_passed"]
    assert len(saved["cases"]) == 2
    printed = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert printed["boundaries"] == 30
    assert printed["case"] == "comparison"


def test_cli_rejects_overwriting_a_comparison_input_before_any_write(completed, tmp_path, monkeypatch):
    _, reports = completed
    monkeypatch.chdir(tmp_path)
    first = tmp_path / "baseline.json"
    second = tmp_path / "stress.json"
    first.write_text(json.dumps(reports[0]))
    second.write_text(json.dumps(reports[1]))
    before = first.read_bytes(), second.read_bytes()
    with pytest.raises(SystemExit, match="distinct from the output"):
        cli().main(["--compare", "baseline.json", "stress.json", "--output", "baseline.json"])
    assert (first.read_bytes(), second.read_bytes()) == before
