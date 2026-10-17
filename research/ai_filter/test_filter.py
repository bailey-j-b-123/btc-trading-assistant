from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from filter import FilterError, LogisticTakeRejectFilter, TrainingExample

BASE = datetime(2023, 4, 1, tzinfo=timezone.utc)

def features(i):
    return {"direction": "LONG" if i % 2 == 0 else "SHORT", "atr_pct": 0.01 + i * .001,
            "trend_strength_atr": (i % 5) * .2, "directional_momentum24": (i - 5) * .1,
            "relative_volume24": 0.8 + (i % 4) * .1, "rsi14": 35 + i * 3}

def fixture():
    return [TrainingExample(features(i), 1.0 if i % 2 == 0 else -0.5,
                           BASE + timedelta(days=i), BASE + timedelta(days=i, hours=2)) for i in range(8)]

def trained():
    return LogisticTakeRejectFilter(steps=400).fit(fixture(), training_cutoff=BASE + timedelta(days=8))

def test_deterministic_fit_predict_and_versioned_fingerprint():
    a, b = trained(), trained()
    assert a.predict(features(2)) == b.predict(features(2))
    assert a.predict(features(2)).model_version == "brain-ai-filter-logistic-v1"
    assert len(a.predict(features(2)).fingerprint) == 64

def test_prediction_interface_takes_features_only_and_no_outcome_metadata():
    model = trained()
    prediction = model.predict(features(3))
    assert prediction.decision in {"TAKE", "REJECT"}
    assert 0 <= prediction.probability <= 1
    assert set(prediction.__dataclass_fields__) == {"decision", "probability", "model_version", "fingerprint"}
    with pytest.raises(FilterError):
        model.predict({**features(3), "net_r": 2, "outcome_time": BASE})

@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), -float("inf"), "0.2"])
def test_invalid_features_fail_closed(bad):
    candidate = features(0)
    candidate["atr_pct"] = bad
    with pytest.raises(FilterError):
        trained().predict(candidate)

def test_missing_feature_rejects():
    candidate = features(0)
    del candidate["rsi14"]
    with pytest.raises(FilterError):
        trained().predict(candidate)

def test_invalid_direction_rejects():
    candidate = features(0)
    candidate["direction"] = "FLAT"
    with pytest.raises(FilterError):
        trained().predict(candidate)

def test_chronological_training_cutoff_and_test_gap_enforced():
    rows = fixture()
    with pytest.raises(FilterError, match="crosses training_cutoff"):
        LogisticTakeRejectFilter().fit(rows, training_cutoff=BASE + timedelta(days=3))
    with pytest.raises(FilterError, match="strictly after"):
        LogisticTakeRejectFilter().fit(rows, training_cutoff=BASE + timedelta(days=8), test_start=BASE + timedelta(days=8))
    model = LogisticTakeRejectFilter().fit(rows, training_cutoff=BASE + timedelta(days=8), test_start=BASE + timedelta(days=9))
    assert model.fitted

def test_completed_candle_label_timing_requires_outcome_after_signal():
    rows = fixture()
    rows[0] = TrainingExample(features(0), 1.0, BASE, BASE)
    with pytest.raises(FilterError, match="outcome_time must follow signal_time"):
        LogisticTakeRejectFilter().fit(rows, training_cutoff=BASE + timedelta(days=8))

def test_save_load_preserves_score_and_detects_tampering(tmp_path):
    model = trained()
    before = model.predict(features(4))
    path = tmp_path / "model.json"
    model.save(path)
    loaded = LogisticTakeRejectFilter.load(path)
    assert loaded.predict(features(4)) == before
    data = json.loads(path.read_text())
    data["threshold"] = .6
    path.write_text(json.dumps(data))
    with pytest.raises(FilterError, match="fingerprint mismatch"):
        LogisticTakeRejectFilter.load(path)

def test_training_labels_must_be_finite_and_both_classes_present():
    rows = fixture()
    rows[0] = TrainingExample(features(0), math.nan, rows[0].signal_time, rows[0].outcome_time)
    with pytest.raises(FilterError, match="net_r must be finite"):
        LogisticTakeRejectFilter().fit(rows, training_cutoff=BASE + timedelta(days=8))
