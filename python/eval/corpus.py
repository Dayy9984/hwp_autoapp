# -*- coding: utf-8 -*-
"""Corpus loading for the headless auto-eval harness.

A *case* describes one form to run through the edit -> verify pipeline:

    {
      "name": str,                         # human label (unique within a run)
      "template_hwp_path": str,            # absolute path to the template HWP/HWPX
      "diff_json_path": str | None,        # ground truth: diff.json (preferred)
      "filled_cvd_path": str | None,       # fallback ground truth: filled.cvd.md
      "instruction": str | None,           # natural-language intent (MODE B user_intent)
      "source": str,                       # provenance tag (fs / db / manifest)
    }

Sources
-------
1. Filesystem scan of ``template_pairs`` directories. A configurable root is
   scanned for ``template_pairs/<pairId>/`` dirs (or a single such dir, or a
   parent that *contains* a ``template_pairs`` folder). Each pair dir must hold
   a template HWP plus a ground-truth file (``diff.json`` or ``filled.cvd.md``).
2. SQLite DB (the app's better-sqlite3 file is standard SQLite). The
   ``template_pairs`` table gives ``template_rel_path`` resolved against the
   project base ``<userData>/projects/<project_id>/``.
3. A manifest JSON listing explicit cases.

All loaders degrade gracefully: a missing/empty corpus yields ``[]`` and the
caller prints a clear "no corpus found" message rather than crashing.
"""

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

# Candidate template file names inside a pair dir (first match wins).
_TEMPLATE_NAMES = ("template.hwp", "template.hwpx", "template.HWP", "template.HWPX")
_DIFF_NAME = "diff.json"
_FILLED_CVD_NAME = "filled.cvd.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_template_hwp(pair_dir: str) -> Optional[str]:
    """Return the template HWP/HWPX path inside ``pair_dir`` or None."""
    for name in _TEMPLATE_NAMES:
        candidate = os.path.join(pair_dir, name)
        if os.path.isfile(candidate):
            return candidate
    # Fallback: any template.* file (e.g. odd casing/extension).
    try:
        for entry in os.listdir(pair_dir):
            low = entry.lower()
            if low.startswith("template.") and low.rsplit(".", 1)[-1] in ("hwp", "hwpx"):
                return os.path.join(pair_dir, entry)
    except OSError:
        pass
    return None


def _pair_case_from_dir(pair_dir: str, source: str = "fs") -> Optional[Dict[str, Any]]:
    """Build a case from a single ``template_pairs/<pairId>`` directory."""
    pair_dir = os.path.abspath(pair_dir)
    template_hwp = _find_template_hwp(pair_dir)
    if not template_hwp:
        return None

    diff_path = os.path.join(pair_dir, _DIFF_NAME)
    filled_cvd_path = os.path.join(pair_dir, _FILLED_CVD_NAME)
    has_diff = os.path.isfile(diff_path)
    has_filled = os.path.isfile(filled_cvd_path)
    if not has_diff and not has_filled:
        return None

    return {
        "name": os.path.basename(pair_dir),
        "template_hwp_path": template_hwp,
        "diff_json_path": diff_path if has_diff else None,
        "filled_cvd_path": filled_cvd_path if has_filled else None,
        "instruction": _read_instruction(pair_dir),
        "source": source,
    }


def _read_instruction(pair_dir: str) -> Optional[str]:
    """Optional natural-language intent from ``instruction.txt`` in a pair dir."""
    path = os.path.join(pair_dir, "instruction.txt")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read().strip()
            return text or None
        except OSError:
            return None
    return None


def _dedupe_names(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure unique case names (suffix ``#2``, ``#3`` ... on collision)."""
    seen: Dict[str, int] = {}
    for case in cases:
        base = case.get("name") or "case"
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count > 1:
            case["name"] = f"{base}#{count}"
    return cases


# ---------------------------------------------------------------------------
# Filesystem scan
# ---------------------------------------------------------------------------

def load_from_filesystem(root: str) -> List[Dict[str, Any]]:
    """Scan ``root`` for template-pair directories.

    ``root`` may be:
      - a single ``template_pairs/<pairId>`` dir (contains a template + diff),
      - a ``template_pairs`` dir (iterate its child pair dirs),
      - any ancestor dir that *contains* a ``template_pairs`` folder (recursed).
    """
    root = os.path.abspath(root)
    cases: List[Dict[str, Any]] = []

    if not os.path.isdir(root):
        return cases

    # Case A: root itself is a pair dir.
    direct = _pair_case_from_dir(root)
    if direct:
        return [direct]

    # Case B: root is a template_pairs dir -> iterate children.
    if os.path.basename(root.rstrip(os.sep)) == "template_pairs":
        cases.extend(_scan_template_pairs_dir(root))
        return _dedupe_names(cases)

    # Case C: root contains template_pairs folder(s) somewhere beneath it.
    for current, dirs, _files in os.walk(root):
        if os.path.basename(current) == "template_pairs":
            cases.extend(_scan_template_pairs_dir(current))
            # Do not descend into pair dirs themselves.
            dirs[:] = []
    return _dedupe_names(cases)


def _scan_template_pairs_dir(tp_dir: str) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    try:
        entries = sorted(os.listdir(tp_dir))
    except OSError:
        return cases
    for entry in entries:
        pair_dir = os.path.join(tp_dir, entry)
        if not os.path.isdir(pair_dir):
            continue
        case = _pair_case_from_dir(pair_dir)
        if case:
            cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# SQLite DB
# ---------------------------------------------------------------------------

def load_from_db(
    db_path: str,
    user_data_path: str,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load cases from the app's SQLite DB ``template_pairs`` table.

    Args:
        db_path: path to the SQLite database file.
        user_data_path: the Electron ``userData`` dir (project files live under
            ``<user_data_path>/projects/<project_id>/``).
        project_id: restrict to one project (None = all projects).

    Each on-disk pair dir is then resolved and validated via the filesystem
    loader so the resulting cases match the fs-scan shape exactly.
    """
    cases: List[Dict[str, Any]] = []
    if not os.path.isfile(db_path):
        return cases

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        if project_id:
            rows = conn.execute(
                "SELECT id, project_id, template_rel_path FROM template_pairs "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, project_id, template_rel_path FROM template_pairs"
            ).fetchall()
    except sqlite3.Error:
        return cases
    finally:
        if conn is not None:
            conn.close()

    for row in rows:
        pid = row["project_id"]
        template_rel = row["template_rel_path"] or ""
        project_base = os.path.join(user_data_path, "projects", str(pid))
        template_abs = os.path.join(project_base, template_rel)
        # The pair dir is the directory containing the template file.
        pair_dir = os.path.dirname(template_abs)
        case = _pair_case_from_dir(pair_dir, source="db")
        if case:
            case["name"] = f"{pid}/{row['id']}"
            cases.append(case)
    return _dedupe_names(cases)


# ---------------------------------------------------------------------------
# Manifest JSON
# ---------------------------------------------------------------------------

def load_from_manifest(manifest_path: str) -> List[Dict[str, Any]]:
    """Load explicit cases from a manifest JSON file.

    Manifest schema (either a top-level list or ``{"cases": [...]}``):
        [
          {
            "name": "case-1",
            "template_hwp_path": "C:/.../template.hwp",
            "diff_json_path": "C:/.../diff.json",      # or filled_cvd_path
            "instruction": "..."                         # optional
          }, ...
        ]

    Relative paths are resolved against the manifest file's directory.
    """
    cases: List[Dict[str, Any]] = []
    if not os.path.isfile(manifest_path):
        return cases

    try:
        with open(manifest_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return cases

    raw_cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(raw_cases, list):
        return cases

    base_dir = os.path.dirname(os.path.abspath(manifest_path))

    def _resolve(p: Optional[str]) -> Optional[str]:
        if not p:
            return None
        return p if os.path.isabs(p) else os.path.abspath(os.path.join(base_dir, p))

    for idx, raw in enumerate(raw_cases):
        if not isinstance(raw, dict):
            continue
        template = _resolve(raw.get("template_hwp_path") or raw.get("template_path"))
        if not template:
            continue
        diff = _resolve(raw.get("diff_json_path") or raw.get("diff_path"))
        filled = _resolve(raw.get("filled_cvd_path") or raw.get("filled_cvd"))
        cases.append({
            "name": raw.get("name") or f"manifest-case-{idx + 1}",
            "template_hwp_path": template,
            "diff_json_path": diff,
            "filled_cvd_path": filled,
            "instruction": raw.get("instruction"),
            "source": "manifest",
        })
    return _dedupe_names(cases)


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def load_corpus(
    source: str,
    user_data_path: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load a corpus from ``source`` (auto-detecting its kind).

    Args:
        source: a manifest ``.json`` file, a SQLite ``.db``/``.sqlite`` file,
            or a directory to scan for ``template_pairs``.
        user_data_path: required when ``source`` is a DB (to resolve rel paths).
            Defaults to two levels above the DB file if not given.
        project_id: optional project filter for DB sources.

    Returns:
        A list of case dicts (possibly empty — never raises on missing corpus).
    """
    if not source:
        return []

    source = os.path.abspath(source)

    if os.path.isfile(source):
        lower = source.lower()
        if lower.endswith(".json"):
            return load_from_manifest(source)
        if lower.endswith((".db", ".sqlite", ".sqlite3")):
            udp = user_data_path or os.path.dirname(os.path.dirname(source))
            return load_from_db(source, udp, project_id=project_id)
        # Unknown file type — try manifest parse as a last resort.
        return load_from_manifest(source)

    if os.path.isdir(source):
        return load_from_filesystem(source)

    return []


def load_diff_changes(diff_json_path: str) -> List[Dict[str, Any]]:
    """Read the ``changes`` list out of a diff.json file (``[]`` on failure)."""
    if not diff_json_path or not os.path.isfile(diff_json_path):
        return []
    try:
        with open(diff_json_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    changes = data.get("changes") if isinstance(data, dict) else None
    return changes if isinstance(changes, list) else []
