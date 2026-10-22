"""Resolves which CAM template file a deal type should use.

Kept free of any dependency beyond the standard library (no anthropic/docx/
openpyxl imports) so it can be unit tested without installing the full
project requirements, and imported by both orchestrator.py and
calibrate.py without pulling in the Anthropic client.
"""
import os

CAM_TEMPLATES_DIR = os.path.join("templates", "cam")
LOCAL_CAM_TEMPLATES_DIR = os.path.join("templates", "local", "cam")


def _cam_dir(base_dir):
    return os.path.join(base_dir, CAM_TEMPLATES_DIR) if base_dir else CAM_TEMPLATES_DIR


def _local_cam_dir(base_dir):
    return os.path.join(base_dir, LOCAL_CAM_TEMPLATES_DIR) if base_dir else LOCAL_CAM_TEMPLATES_DIR


def cam_template_path(deal_type, base_dir=None):
    """Return the template file to use for this deal type, or None if there isn't one yet.

    A calibration-derived (or previously auto-saved) override under
    templates/local/cam/ always takes precedence over the shipped default
    under templates/cam/, since it reflects this user's own samples.
    """
    local_path = os.path.join(_local_cam_dir(base_dir), f"{deal_type.lower()}_cam.md")
    if os.path.exists(local_path):
        return local_path

    default_path = os.path.join(_cam_dir(base_dir), f"{deal_type.lower()}_cam.md")
    if os.path.exists(default_path):
        return default_path

    return None


def local_cam_template_path(deal_type, base_dir=None):
    """Path to write a new override/auto-saved template for this deal type.

    Always under templates/local/cam/ (git-ignored) -- never the shipped
    templates/cam/ defaults, since anything written here (a calibration
    output, or a first real draft auto-saved as a starting template) may
    carry real, user-specific content.
    """
    return os.path.join(_local_cam_dir(base_dir), f"{deal_type.lower()}_cam.md")
