from copy import deepcopy

import pytest
from PIL import Image
from pydantic import ValidationError
from test_material_service import evidence
from test_yolo_material import classifier, wall

from agents.material import aggregation, yolo
from agents.material.config import MaterialConfig, material_config


def run(observations, images):
    return aggregation.aggregate(
        observations,
        images,
        model="fixture",
        is_demo=False,
        project_material=None,
        target_confirmed=True,
    )


@pytest.mark.parametrize("score,accepted", [(0.7999, False), (0.8, True), (0.85, True)])
def test_crop_threshold_boundary_preserves_raw_prediction(tmp_path, monkeypatch, score, accepted):
    predictor, _ = classifier(tmp_path, monkeypatch, conf=score)
    obs = predictor.predict_walls(Image.new("RGB", (200, 200)), "img", [wall()])[0]
    assert obs.predicted_material == "brick"
    assert obs.material_conf == score
    assert obs.material == ("brick" if accepted else "unclear")
    assert obs.visibility == ("visible" if accepted else "unclear")


def test_external_adapter_cannot_bypass_crop_threshold_with_other_confidence():
    observations, images = evidence(confidence=0.99)
    for obs in observations:
        obs.material_conf = 0.79
        obs.included = True
    result = run(observations, images)
    assert result.material == "unclear" and result.votes == {}
    assert all(not obs.included and obs.predicted_material == "brick" for obs in result.per_view)
    assert all(obs.exclusion_reason == "LOW_CONFIDENCE" for obs in result.per_view)


def test_yaml_policy_changes_classifier_and_aggregation_together(tmp_path, monkeypatch):
    config = material_config().model_copy(update={"min_crop_confidence": 0.9})
    monkeypatch.setattr(yolo, "material_config", lambda: config)
    monkeypatch.setattr(aggregation, "material_config", lambda: config)
    predictor, _ = classifier(tmp_path, monkeypatch, conf=0.85)
    assert (
        predictor.predict_walls(Image.new("RGB", (200, 200)), "img", [wall()])[0].material
        == "unclear"
    )
    observations, images = evidence(confidence=0.85)
    assert run(observations, images).votes == {}


def test_object_threshold_is_separate_from_crop_threshold(monkeypatch):
    config = material_config().model_copy(update={"min_object_confidence": 0.9})
    monkeypatch.setattr(aggregation, "material_config", lambda: config)
    observations, images = evidence(confidence=0.85)
    result = run(observations, images)
    assert result.material == "brick" and result.votes == {"brick": 1}
    assert result.status == "needs_review" and not result.fixed_on_object
    assert result.rules == config.version
    assert result.thresholds["min_object_confidence"] == 0.9


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_crop_confidence", 0),
        ("min_crop_confidence", 1.1),
        ("min_object_confidence", float("nan")),
        ("min_wall_confidence", -0.1),
        ("min_confirmed_facades", 5),
        ("mixed_material_share", 0.7),
        ("min_crop_px", 0),
    ],
)
def test_invalid_threshold_config_is_rejected(field, value):
    value_config = deepcopy(material_config().model_dump(mode="json"))
    value_config[field] = value
    with pytest.raises(ValidationError):
        MaterialConfig.model_validate(value_config)
