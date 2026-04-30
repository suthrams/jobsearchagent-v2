"""Tests for SkillNormalizer — alias lookup and deduplication."""
import pytest

from app.services.skill_normalizer import SkillNormalizer


@pytest.fixture(scope="module")
def normalizer():
    return SkillNormalizer()  # loads data/skills.yaml from project root


def test_normalize_lowercase(normalizer):
    assert normalizer.normalize("python") == "Python"


def test_normalize_mixed_case(normalizer):
    assert normalizer.normalize("PYTHON") == "Python"
    assert normalizer.normalize("Python") == "Python"


def test_normalize_alias_python3(normalizer):
    assert normalizer.normalize("python3") == "Python"


def test_normalize_k8s(normalizer):
    assert normalizer.normalize("k8s") == "Kubernetes"


def test_normalize_kubernetes_full(normalizer):
    assert normalizer.normalize("kubernetes") == "Kubernetes"


def test_normalize_aws(normalizer):
    assert normalizer.normalize("aws") == "AWS"
    assert normalizer.normalize("amazon web services") == "AWS"


def test_normalize_gcp(normalizer):
    assert normalizer.normalize("gcp") == "GCP"
    assert normalizer.normalize("google cloud") == "GCP"


def test_normalize_already_canonical(normalizer):
    assert normalizer.normalize("Python") == "Python"


def test_normalize_unknown_skill_passthrough(normalizer):
    assert normalizer.normalize("xyz_unknown_tool_42") == "xyz_unknown_tool_42"


def test_normalize_strips_whitespace(normalizer):
    assert normalizer.normalize("  python  ") == "Python"


def test_normalize_list_preserves_order(normalizer):
    result = normalizer.normalize_list(["k8s", "Python", "aws"])
    assert result == ["Kubernetes", "Python", "AWS"]


def test_normalize_list_does_not_deduplicate(normalizer):
    result = normalizer.normalize_list(["python", "Python", "python3"])
    assert result == ["Python", "Python", "Python"]


def test_normalize_and_deduplicate_removes_aliases(normalizer):
    result = normalizer.normalize_and_deduplicate(["python", "Python", "python3"])
    assert result == ["Python"]


def test_normalize_and_deduplicate_sorted(normalizer):
    result = normalizer.normalize_and_deduplicate(["k8s", "aws", "python"])
    assert result == sorted(result)


def test_normalize_and_deduplicate_empty_list(normalizer):
    assert normalizer.normalize_and_deduplicate([]) == []


def test_normalize_and_deduplicate_mixed(normalizer):
    result = normalizer.normalize_and_deduplicate(["k8s", "Kubernetes", "python", "py"])
    assert "Kubernetes" in result
    assert "Python" in result
    assert result.count("Kubernetes") == 1
    assert result.count("Python") == 1


def test_normalize_tf_maps_to_terraform(normalizer):
    assert normalizer.normalize("tf") == "Terraform"
