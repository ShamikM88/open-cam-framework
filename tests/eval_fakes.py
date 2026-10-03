"""Shared fakes for the live-runner tests (issue #151, PR 2). Nothing here can reach a
model: every "client" is a local object returning scripted text, so the whole live path is
exercised with zero model calls and no API key."""
import copy
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import eval_report
from eval_cases import load_dataset
from eval_oracles import build_scripted_output

REPO_ROOT = Path(eval_report.REPO_ROOT)
DECOYS = {
    "local_template": "DECOY-LOCAL-TEMPLATE-MUST-NOT-LEAK",
    "style_guide": "DECOY-STYLE-GUIDE-MUST-NOT-LEAK",
    "credit_policy": "DECOY-CREDIT-POLICY-MUST-NOT-LEAK",
    "credit_policy_notes": "DECOY-POLICY-NOTES-MUST-NOT-LEAK",
    "deal_learnings": "DECOY-DEAL-LEARNINGS-MUST-NOT-LEAK",
    "company_learnings": "DECOY-COMPANY-LEARNINGS-MUST-NOT-LEAK",
}


def dataset_case(case_id):
    case = next(c for c in load_dataset("v1")["cases"] if c["id"] == case_id)
    return copy.deepcopy(case)


class FakeClient:
    """Stands in for the Anthropic client. `respond(kwargs) -> text` decides each reply;
    every call's kwargs are kept so tests can inspect the real prompt that was built."""

    def __init__(self, respond, input_tokens=11, output_tokens=7):
        self.respond = respond
        self.calls = []
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.respond(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text=text)],
            usage=SimpleNamespace(input_tokens=self.input_tokens, output_tokens=self.output_tokens))

    @property
    def last_message(self):
        return self.calls[-1]["messages"][0]["content"]


def verdict_text(verdict="REJECTED", notes="Unsourced market-share claim."):
    return f"Review.\n```json\n{json.dumps({'verdict': verdict, 'notes': notes})}\n```"


def good_maker_text(case):
    return build_scripted_output(case, case["dry_run"]["good"]).draft_text


def bad_maker_text(case):
    return build_scripted_output(case, case["dry_run"]["bad"]).draft_text


def make_fake_repo(tmp_path, decoys=True):
    """A copy of just the files a run needs, plus decoys in every place a leak could come from."""
    root = Path(tmp_path) / "fake-repo"
    shutil.copytree(REPO_ROOT / "agents", root / "agents")
    shutil.copytree(REPO_ROOT / "templates" / "cam", root / "templates" / "cam")
    (root / "config").mkdir()
    shutil.copyfile(REPO_ROOT / "config" / "settings.json", root / "config" / "settings.json")
    if decoys:
        (root / "templates" / "local" / "cam").mkdir(parents=True)
        (root / "templates" / "local" / "cam" / "corporate_credit_cam.md").write_text(DECOYS["local_template"], encoding="utf-8")
        for key in ("style_guide", "credit_policy", "credit_policy_notes", "deal_learnings"):
            (root / "config" / f"{key}.md").write_text(DECOYS[key], encoding="utf-8")
        (root / "deals").mkdir()
    return root


def add_company_decoy(root, company):
    folder = Path(root) / "deals" / company
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "_learnings.md").write_text(DECOYS["company_learnings"], encoding="utf-8")


def listing(path):
    return sorted(os.listdir(path)) if os.path.isdir(path) else []
