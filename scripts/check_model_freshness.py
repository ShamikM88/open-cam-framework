"""Diagnostic report of newer/deprecated Claude model IDs from the installed Anthropic SDK.

The report never makes a model call or network request and never changes the configured model. It is advisory only:
a model change is a behaviour change and belongs in a separate, reviewed PR with an evaluation statement.
"""
import argparse
import ast
import json
import re
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Union, get_args, get_origin

DEFAULT_SETTINGS_PATH = Path("config/settings.json")
DEFAULT_CALIBRATE_PATH = Path(__file__).resolve().with_name("calibrate.py")
CONFIGURED_MODEL_KEYS = ("maker_model", "checker_model", "calibrate_model")
MODEL_ID_RE = re.compile(r"claude-([a-z][a-z0-9]*)-(\d+)(?:-(\d+))?")
DATED_VARIANT_RE = re.compile(r"-\d{8}$")
IGNORED_SUFFIX_RE = re.compile(r"(?:-latest|-preview)$")
_LOAD_FROM_INSTALLED_SDK = object()


def read_default_model(calibrate_path=None):
    """Read the one top-level _DEFAULT_MODEL string literal without importing calibrate.py."""
    path = Path(calibrate_path) if calibrate_path is not None else DEFAULT_CALIBRATE_PATH
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError(f"cannot read the _DEFAULT_MODEL assignment in {path}: {exc}") from exc

    assignments = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_DEFAULT_MODEL" for target in node.targets
        ):
            assignments.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_DEFAULT_MODEL":
            assignments.append(node.value)

    if len(assignments) != 1:
        raise ValueError(f"{path} must have exactly one top-level _DEFAULT_MODEL assignment")
    try:
        value = ast.literal_eval(assignments[0])
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError(f"{path} _DEFAULT_MODEL must be a string literal") from exc
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} _DEFAULT_MODEL must be a non-empty string literal")
    return value


def load_model_sources(settings_path=None, calibrate_path=None):
    """Return the model IDs the framework can select, with their source locations."""
    path = Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read configuration {path}: {exc}") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"configuration {path} must contain a JSON object")

    models = []
    for key in CONFIGURED_MODEL_KEYS:
        if key not in settings or settings[key] is None or settings[key] == "":
            continue
        value = settings[key]
        if not isinstance(value, str):
            raise ValueError(f"configuration {path} field {key!r} must be a string when set")
        models.append({"source": f"config/settings.json:{key}", "model_id": value})

    default_model = read_default_model(calibrate_path)
    models.append({"source": "scripts/calibrate.py:_DEFAULT_MODEL", "model_id": default_model})
    return models


def _is_union(value):
    return get_origin(value) in (Union, types.UnionType)


def extract_known_model_ids(model_type):
    """Extract the SDK's known models only when Model retains its documented Union[Literal[...], ...] shape."""
    if not _is_union(model_type):
        return None
    members = get_args(model_type)
    if len(members) < 2 or getattr(get_origin(members[0]), "__name__", "") != "Literal":
        return None
    values = get_args(members[0])
    if not values or any(not isinstance(value, str) or not value for value in values):
        return None
    return frozenset(values)


def extract_deprecated_models(table):
    """Return the SDK's model-to-end-of-life mapping, or None for a missing/reshaped table."""
    if not isinstance(table, Mapping):
        return None
    result = {}
    for model_id, end_of_life in table.items():
        if (not isinstance(model_id, str) or not model_id
                or not isinstance(end_of_life, str) or not end_of_life.strip()):
            return None
        result[model_id] = end_of_life.strip()
    return result


def is_ignored_variant(model_id):
    """Dated snapshots and moving/preview aliases are deliberately excluded from version comparison."""
    return bool(IGNORED_SUFFIX_RE.search(model_id) or DATED_VARIANT_RE.search(model_id))


def parse_model_id(model_id):
    """Parse stable claude-<family>-<major>[-<minor>] names; return None for aliases/date snapshots."""
    if not isinstance(model_id, str) or is_ignored_variant(model_id):
        return None
    match = MODEL_ID_RE.fullmatch(model_id)
    if not match:
        return None
    family, major, minor = match.groups()
    return {"family": family, "major": int(major), "minor": int(minor or "0")}


def build_report(model_sources, model_type, deprecated_table, sdk_version=None):
    """Make the report from SDK tables supplied as arguments; None/changed shapes are reported, never guessed."""
    known_models = extract_known_model_ids(model_type)
    deprecated_models = extract_deprecated_models(deprecated_table)

    sdk_tables = {
        "known_models": {
            "source": "anthropic.types.Model",
            "status": "available" if known_models is not None else "SDK table not found",
            "count": len(known_models) if known_models is not None else None,
        },
        "deprecated_models": {
            "source": "anthropic.resources.messages.messages.DEPRECATED_MODELS",
            "status": "available" if deprecated_models is not None else "SDK table not found",
            "count": len(deprecated_models) if deprecated_models is not None else None,
        },
    }

    findings = []
    for details in sdk_tables.values():
        if details["status"] != "available":
            findings.append({
                "type": "sdk_table_not_found",
                "table": details["source"],
                "message": f"SDK table not found: {details['source']}",
            })

    stable_candidates = []
    if known_models is not None:
        for candidate in known_models:
            parsed = parse_model_id(candidate)
            if parsed is not None:
                stable_candidates.append((candidate, parsed))

    models = []
    for item in model_sources:
        model_id = item["model_id"]
        parsed = parse_model_id(model_id)
        known_in_sdk = None if known_models is None else model_id in known_models
        ignored_variant = is_ignored_variant(model_id) and (known_models is None or known_in_sdk is True)

        newer_model = None
        if parsed is not None and known_models is not None and known_in_sdk:
            newer = [
                (candidate, candidate_version)
                for candidate, candidate_version in stable_candidates
                if candidate_version["family"] == parsed["family"]
                and (candidate_version["major"], candidate_version["minor"]) > (parsed["major"], parsed["minor"])
            ]
            if newer:
                newer_model = max(newer, key=lambda pair: (pair[1]["major"], pair[1]["minor"]))[0]
                findings.append({
                    "type": "newer_version",
                    "source": item["source"],
                    "model_id": model_id,
                    "suggested_model": newer_model,
                    "message": f"{model_id} has a newer stable model in the same family: {newer_model}",
                })

        unknown = False
        if not ignored_variant:
            if parsed is None:
                unknown = True
            elif known_models is not None and known_in_sdk is False:
                unknown = True
        if unknown:
            findings.append({
                "type": "unknown_model",
                "source": item["source"],
                "model_id": model_id,
                "message": f"unknown model ID: {model_id}",
            })

        if deprecated_models is None:
            deprecated = None
            end_of_life = None
        else:
            deprecated = model_id in deprecated_models
            end_of_life = deprecated_models.get(model_id)
            if deprecated:
                findings.append({
                    "type": "deprecated_model",
                    "source": item["source"],
                    "model_id": model_id,
                    "end_of_life": end_of_life,
                    "message": f"{model_id} is deprecated; SDK end-of-life date: {end_of_life}",
                })

        if unknown:
            status = "unknown"
        elif deprecated is True and newer_model:
            status = "deprecated; newer version available"
        elif deprecated is True:
            status = "deprecated"
        elif newer_model:
            status = "newer version available"
        elif ignored_variant:
            status = "ignored variant"
        elif known_models is None or deprecated_models is None:
            status = "SDK table not found"
        else:
            status = "no finding"

        models.append({
            "source": item["source"],
            "model_id": model_id,
            "parsed_version": parsed,
            "known_in_sdk": known_in_sdk,
            "newer_model": newer_model,
            "deprecated": deprecated,
            "end_of_life": end_of_life,
            "status": status,
        })

    freshness_note = (
        "This report is only as fresh as the installed Anthropic SDK. It reads the SDK's packaged model tables; "
        "it does not query the API, make a network request, or call a model."
    )
    return {
        "report_version": 1,
        "sdk_version": sdk_version,
        "freshness_note": freshness_note,
        "sdk_tables": sdk_tables,
        "models": models,
        "findings": findings,
        "summary": {
            "models_checked": len(models),
            "finding_count": len(findings),
            "has_findings": bool(findings),
        },
    }


def load_installed_sdk_tables():
    """Import only the SDK's two local metadata tables; this performs no API or network request."""
    try:
        from anthropic.types import Model
    except Exception:  # a missing export or a changed SDK module shape is diagnostic, not fatal
        Model = None

    try:
        from anthropic.resources.messages.messages import DEPRECATED_MODELS
    except Exception:  # a missing export or a changed SDK module shape is diagnostic, not fatal
        DEPRECATED_MODELS = None

    try:
        from importlib.metadata import version
        sdk_version = version("anthropic")
    except Exception:
        sdk_version = None
    return Model, DEPRECATED_MODELS, sdk_version


def _load_report_inputs(settings_path, calibrate_path, model_type, deprecated_table, sdk_version):
    model_sources = load_model_sources(settings_path, calibrate_path)
    if (model_type is _LOAD_FROM_INSTALLED_SDK
            or deprecated_table is _LOAD_FROM_INSTALLED_SDK
            or sdk_version is _LOAD_FROM_INSTALLED_SDK):
        installed_model_type, installed_deprecated, installed_version = load_installed_sdk_tables()
        if model_type is _LOAD_FROM_INSTALLED_SDK:
            model_type = installed_model_type
        if deprecated_table is _LOAD_FROM_INSTALLED_SDK:
            deprecated_table = installed_deprecated
        if sdk_version is _LOAD_FROM_INSTALLED_SDK:
            sdk_version = installed_version
    return build_report(model_sources, model_type, deprecated_table, sdk_version)


def render_markdown(report):
    """Render a concise human-readable report with explicit table availability and conservative recommendations."""
    version = report["sdk_version"] or "unknown"
    lines = [
        "# Claude model freshness report",
        "",
        f"Installed Anthropic SDK version: `{version}`.",
        "",
        report["freshness_note"],
        "",
        "## SDK tables",
        "",
        f"- Known-model list (`{report['sdk_tables']['known_models']['source']}`): "
        f"**{report['sdk_tables']['known_models']['status']}**"
        + (f" ({report['sdk_tables']['known_models']['count']} IDs)" if report["sdk_tables"]["known_models"]["count"] is not None else ""),
        f"- Deprecation list (`{report['sdk_tables']['deprecated_models']['source']}`): "
        f"**{report['sdk_tables']['deprecated_models']['status']}**"
        + (f" ({report['sdk_tables']['deprecated_models']['count']} IDs)" if report["sdk_tables"]["deprecated_models"]["count"] is not None else ""),
        "",
        "## Configured model IDs",
        "",
        "| Source | Model ID | Status | Newer stable model (same family) | Deprecated / end of life |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["models"]:
        newer = item["newer_model"] or "—"
        if item["deprecated"] is None:
            eol = "SDK table not found"
        elif item["deprecated"]:
            eol = f"Yes — {item['end_of_life']}"
        else:
            eol = "No"
        status = item["status"]
        lines.append(f"| {item['source']} | `{item['model_id']}` | {status} | `{newer}` | {eol} |")

    lines.extend(["", "## Findings", ""])
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(f"- {finding['message']}")
    else:
        lines.append("No findings.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None, *, settings_path=None, calibrate_path=None, model_type=_LOAD_FROM_INSTALLED_SDK,
         deprecated_table=_LOAD_FROM_INSTALLED_SDK, sdk_version=_LOAD_FROM_INSTALLED_SDK):
    parser = argparse.ArgumentParser(
        description="Report newer and deprecated Claude model IDs from the installed SDK. Diagnostic only; never upgrades models.")
    parser.add_argument("--json", dest="json_path", metavar="PATH", help="also write the report as JSON to PATH")
    args = parser.parse_args(argv)

    try:
        report = _load_report_inputs(
            Path(settings_path) if settings_path is not None else DEFAULT_SETTINGS_PATH,
            Path(calibrate_path) if calibrate_path is not None else DEFAULT_CALIBRATE_PATH,
            model_type,
            deprecated_table,
            sdk_version,
        )
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    markdown = render_markdown(report)
    if args.json_path:
        try:
            with open(args.json_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
                f.write("\n")
        except OSError as exc:
            print(f"error: cannot write JSON report {args.json_path}: {exc}", file=sys.stderr)
            return 1
    print(markdown)
    return 0


if __name__ == "__main__":
    from textio import configure_stdio
    configure_stdio()
    sys.exit(main())
