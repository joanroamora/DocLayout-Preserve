"""Unit tests for Feature branch governance and GitHub Actions CI/DevSecOps workflows."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from doclayout.core.governance import main as governance_cli
from doclayout.core.governance import validate_feature_branch_name

_PINNED_ACTION_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "branch_name",
    [
        "Feature1EntornoYScaffolding",
        "Feature2IngestaYParsingLayout",
        "Feature7PipelineCICDSeguridad",
        "Feature11PipelinePromocionYRollback",
        "refs/heads/Feature9InfraestructuraTerraform",
    ],
)
def test_valid_feature_branch_names(branch_name: str) -> None:
    """Verify valid PascalCase Feature<PositiveInt><Name> branches pass validation."""
    assert validate_feature_branch_name(branch_name) is True
    assert governance_cli([branch_name]) == 0


@pytest.mark.parametrize(
    "invalid_branch",
    [
        "feature1Lowercase",
        "Feature0ZeroNotAllowed",
        "Feature1lowercaseAfterDigit",
        "Feature1_WithUnderscore",
        "Feature1-WithHyphen",
        "Feature1 WithSpace",
        "Feature1ConTildé",
        "FeatureSinNumero",
        "main",
        "dev",
    ],
)
def test_invalid_feature_branch_names_rejected_on_pr(invalid_branch: str) -> None:
    """Verify non-conforming or accented/protected names fail strict PR head validation."""
    assert validate_feature_branch_name(invalid_branch, allow_protected=False) is False
    assert governance_cli([invalid_branch]) == 1


def test_protected_branches_allowed_when_flag_enabled() -> None:
    """Verify main and dev pass when --allow-protected is explicitly supplied on push."""
    assert validate_feature_branch_name("main", allow_protected=True) is True
    assert validate_feature_branch_name("dev", allow_protected=True) is True
    assert governance_cli(["main", "--allow-protected"]) == 0
    assert governance_cli([]) == 2


@pytest.mark.parametrize(
    "workflow_filename",
    ["ci-validation.yml", "ci-security.yml"],
)
def test_github_workflows_security_and_sha_pinning(workflow_filename: str) -> None:
    """Verify workflows enforce least-privilege permissions, 0-token env, and 40-char SHA pinning."""
    wf_path = _REPO_ROOT / ".github" / "workflows" / workflow_filename
    assert wf_path.exists()

    parsed: dict[str, Any] = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    assert parsed["permissions"] == {"contents": "read"}
    assert parsed["env"]["GEMINI_API_KEY"] == ""

    jobs: dict[str, Any] = parsed["jobs"]
    assert len(jobs) >= 3

    for job_id, job_def in jobs.items():
        for step in job_def.get("steps", []):
            if "uses" in step:
                uses_val = str(step["uses"]).strip()
                assert _PINNED_ACTION_RE.fullmatch(uses_val), (
                    f"Action '{uses_val}' in {workflow_filename}:{job_id} is not pinned "
                    "to an immutable 40-char commit SHA."
                )


def test_dockerfile_and_dockerignore_hardening() -> None:
    """Verify Dockerfile enforces non-root UID 10001 and .dockerignore blocks secrets."""
    dockerfile_text = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile_text
    assert "GEMINI_API_KEY" not in dockerfile_text

    dockerignore_text = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in dockerignore_text
    assert "private/" in dockerignore_text
    assert "*sa*.json" in dockerignore_text
