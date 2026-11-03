"""Scorer calibration validation, without Azure calls or harmful test text."""

import hashlib

import pytest

import safety_calibrate as calibration


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_official_source_requires_exact_checksum():
    with pytest.raises(ValueError, match="checksum mismatch"):
        calibration.official_samples(b"raise RuntimeError('must never execute')")


def test_extracts_only_literals_without_executing_downloaded_code(monkeypatch):
    source = "raise RuntimeError('must never execute')\n"
    for metric in calibration.METRICS:
        source += f"""
def test_content_safety_evaluator_{metric}(self):
    score = eval_fn(query="Safe question", response="Safe response")
"""
        if metric != "sexual":
            source += """
    simple_conversation["messages"][2]["content"] = "Fixture question"
    simple_conversation["messages"][3]["content"] = "Fixture response"
"""
    encoded = source.encode()
    monkeypatch.setattr(calibration, "SOURCE_SHA256", hashlib.sha256(encoded).hexdigest())
    samples = calibration.official_samples(encoded)
    assert len(samples) == 7
    assert sum(sample["positive"] for sample in samples) == 3
    assert not any(sample["metric"] == "sexual" and sample["positive"] for sample in samples)


def sample(positive=False):
    return {"id": "test", "metric": "violence", "positive": positive}


def item(score=0, passed=True, name="violence", status="completed", sample_id="test"):
    return {
        "datasource_item": {"id": sample_id}, "status": status,
        "results": [{"name": name, "score": score, "passed": passed}],
    }


@pytest.mark.parametrize("positive,score,passed,matches,tripped", [
    (False, 0, True, True, False),
    (False, 0, False, False, True),
    (False, 2, True, False, False),
    (True, 2, True, True, False),
    (True, 4, False, True, True),
    (True, 0, True, False, False),
])
def test_detection_is_not_the_same_as_a_threshold_failure(positive, score, passed, matches, tripped):
    result = calibration.summarize([sample(positive)], [item(score, passed)])[0]
    assert result["matches_source_label"] is matches
    assert result["tripped"] is tripped


@pytest.mark.parametrize("items", [
    [], [item(), item()], [item(sample_id="unknown")], [item(name="wrong")],
    [item(status="failed")], [item(score=None)], [item(score=float("nan"))],
    [item(score=8)], [item(passed=None)], [item(score=True)],
])
def test_missing_duplicate_invalid_or_errored_results_are_not_success(items):
    with pytest.raises(ValueError):
        calibration.summarize([sample()], items)


@pytest.mark.anyio
async def test_prepare_does_not_load_azure_settings_or_submit(monkeypatch, tmp_path, capsys):
    (tmp_path / "official_fixtures.py").write_bytes(b"fixture")
    monkeypatch.setattr(calibration, "official_samples", lambda _: [sample()])

    def deny_settings():
        raise AssertionError("Prepare must not load Azure configuration.")

    monkeypatch.setattr(calibration, "load_settings", deny_settings)
    assert await calibration.run(tmp_path, False) == 0
    assert "No Azure calls" in capsys.readouterr().out
    assert not (tmp_path / "run.json").exists()
