import os

from template_resolver import cam_template_path, local_cam_template_path


def _write(path, content="content"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_no_template_returns_none(tmp_path):
    assert cam_template_path("asset_finance", base_dir=str(tmp_path)) is None


def test_falls_back_to_shipped_default_when_no_override_exists(tmp_path):
    default_path = os.path.join(str(tmp_path), "templates", "cam", "asset_finance_cam.md")
    _write(default_path)

    assert cam_template_path("asset_finance", base_dir=str(tmp_path)) == default_path


def test_local_override_takes_precedence_over_default(tmp_path):
    default_path = os.path.join(str(tmp_path), "templates", "cam", "asset_finance_cam.md")
    override_path = os.path.join(str(tmp_path), "templates", "local", "cam", "asset_finance_cam.md")
    _write(default_path, "default")
    _write(override_path, "override")

    assert cam_template_path("asset_finance", base_dir=str(tmp_path)) == override_path


def test_lookup_is_case_insensitive_on_deal_type(tmp_path):
    default_path = os.path.join(str(tmp_path), "templates", "cam", "asset_finance_cam.md")
    _write(default_path)

    assert cam_template_path("Asset_Finance", base_dir=str(tmp_path)) == default_path


def test_local_cam_template_path_does_not_require_the_file_to_exist(tmp_path):
    path = local_cam_template_path("new_deal_type", base_dir=str(tmp_path))
    assert path == os.path.join(str(tmp_path), "templates", "local", "cam", "new_deal_type_cam.md")
    assert not os.path.exists(path)
