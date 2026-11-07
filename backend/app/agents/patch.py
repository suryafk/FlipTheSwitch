"""Patch artifacts: canonical diff hashing, test execution, post-checks (deterministic code).

The hash and test counts are always computed here, never taken from an LLM (docs/03 §1).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from app.agents.common import get_agent_settings

ALLOWED_PREFIXES = ("config/", "src/", "tests/")
FORBIDDEN = ("tests/conftest.py",)
MAX_DIFF_LINES = 200


def git(repo: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, text=True
    ).stdout


def canonical_diff(repo: Path, base: str, commit: str) -> str:
    return git(
        repo, "diff", "--binary", "--full-index", "--no-color", "--no-ext-diff", base, commit
    )


def patch_hash(repo: Path, base: str, commit: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_diff(repo, base, commit).encode()).hexdigest()


def changed_files(repo: Path) -> list[dict[str, str]]:
    out = git(repo, "status", "--porcelain", "--untracked-files=all")
    return [
        {"status": line[:2].strip(), "path": line[3:]} for line in out.splitlines() if line.strip()
    ]


def post_checks(repo: Path) -> list[str]:
    """Return a list of violations (empty = pass)."""
    problems = []
    files = changed_files(repo)
    if not files:
        problems.append("no files changed")
    for f in files:
        if not f["path"].startswith(ALLOWED_PREFIXES):
            problems.append(f"{f['path']} is outside the allowlist {ALLOWED_PREFIXES}")
        if f["path"] in FORBIDDEN:
            problems.append(f"{f['path']} must not be modified")
        if f["status"] == "D" and f["path"].startswith("tests/"):
            problems.append(f"test file deleted: {f['path']}")
    added = git(repo, "diff", "--numstat").splitlines()
    lines = sum(int(a) + int(d) for a, d, _ in (x.split("\t") for x in added) if a.isdigit())
    if lines > MAX_DIFF_LINES:
        problems.append(f"diff too large: {lines} lines")
    return problems


def new_test_names(repo: Path, base: str) -> list[str]:
    diff = git(repo, "diff", base, "--", "tests/") + "".join(
        "".join(f"+{line}\n" for line in (repo / f["path"]).read_text().splitlines())
        for f in changed_files(repo)
        if f["status"] == "??" and f["path"].startswith("tests/")
    )
    return sorted(set(re.findall(r"^\+\s*def (test_\w+)\(", diff, re.MULTILINE)))


def run_tests(repo: Path, select: list[str] | None = None) -> dict[str, Any]:
    """Run the repo's tests with the simulator's toolchain; counts come from JUnit XML."""
    sim = get_agent_settings().sim_dir
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        cmd = [
            "uv",
            "run",
            "--project",
            str(sim),
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit}",
            "tests",
        ]
        if select:
            cmd += ["-k", " or ".join(select)]
        p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=180)
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        failed: list[str] = []
        if junit.exists():
            root = ET.parse(junit).getroot()
            suites = [root] if root.tag == "testsuite" else list(root)
            for s in suites:
                for k in counts:
                    counts[k] += int(s.get(k, 0))
                for case in s.iter("testcase"):
                    if case.find("failure") is not None or case.find("error") is not None:
                        failed.append(case.get("name", "?"))
    passed = counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
    return {
        "passed": passed,
        "failed": counts["failures"],
        "errors": counts["errors"],
        "total": counts["tests"],
        "failed_tests": failed,
        "exit_code": p.returncode,
        "output_tail": "\n".join((p.stdout + p.stderr).strip().splitlines()[-25:]),
    }


def new_tests_fail_on_base(repo: Path, base: str, names: list[str]) -> dict[str, Any]:
    """Apply only the new/changed test files onto a clean checkout of `base`; they must fail."""
    with tempfile.TemporaryDirectory() as tmp:
        clean = Path(tmp) / "base"
        subprocess.run(["git", "clone", "-q", str(repo), str(clean)], check=True)
        git(clean, "checkout", "-q", base)
        for f in changed_files(repo):
            if f["path"].startswith("tests/") and (repo / f["path"]).exists():
                dest = clean / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repo / f["path"], dest)
        result = run_tests(clean, select=names)
    return {"fails_on_base": result["failed"] + result["errors"] > 0, **result}
