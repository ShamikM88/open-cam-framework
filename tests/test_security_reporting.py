from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_security_disclosure_policy_and_public_issue_template_are_present():
    policy_path = ROOT / "SECURITY.md"
    issue_template_path = ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
    issue_config_path = ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"
    security_docs_path = ROOT / "docs" / "security.md"

    assert policy_path.is_file(), "the repository must publish a SECURITY.md disclosure policy"
    assert issue_template_path.is_file(), "new public issues must use the confidentiality warning template"
    assert issue_config_path.is_file(), "issue creation must direct reporters to the policy"
    assert security_docs_path.is_file()

    policy = policy_path.read_text(encoding="utf-8").lower()
    issue_template = issue_template_path.read_text(encoding="utf-8").lower()
    issue_config = issue_config_path.read_text(encoding="utf-8").lower()
    security_docs = security_docs_path.read_text(encoding="utf-8")

    assert "private vulnerability reporting" in policy
    assert "do not" in policy and "public issue" in policy
    assert "synthetic data only" in issue_template
    assert "real borrower" in issue_template
    assert "api key" in issue_template
    for heading in ("## problem", "## concrete scenario", "## proposed fix", "## acceptance criteria"):
        assert heading in issue_template
    assert "security.md" in issue_config
    assert "[security policy](../security.md)" in security_docs.lower()
