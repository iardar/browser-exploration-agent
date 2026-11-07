"""Extraction acceptance checks independent of the original QA workflow."""

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from browser_exploration.reporting import write_schemas

ROOT = Path(__file__).resolve().parents[2]


def test_production_package_has_no_host_workflow_imports() -> None:
    """Verify production package has no host workflow imports.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for production package has no host workflow imports.
    """
    for source in (ROOT / "src" / "browser_exploration").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            assert all(module.split(".")[0] != "agentic_qa" for module in modules), source


def test_public_service_imports_when_host_package_is_unavailable() -> None:
    """Verify public service imports when host package is unavailable.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for public service imports when host package is
            unavailable."""
    code = """
import importlib.abc
import sys

class RejectHost(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "agentic_qa" or fullname.startswith("agentic_qa."):
            raise ImportError("Host package is unavailable")
        return None

sys.meta_path.insert(0, RejectHost())
sys.path.insert(0, sys.argv[1])
from browser_exploration import BrowserExplorer, BrowserExplorationTask, BrowserExplorationResult
from browser_exploration.explorer import AutonomousBrowserExplorer
assert not any(name == "agentic_qa" or name.startswith("agentic_qa.") for name in sys.modules)
print(AutonomousBrowserExplorer.__module__)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(ROOT / "src")],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.stdout.strip() == "browser_exploration.explorer"


def test_checked_in_schemas_match_public_models(tmp_path: Path) -> None:
    """Verify checked in schemas match public models.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for checked in schemas match public models.
    """
    write_schemas(tmp_path)
    published = ROOT / "docs" / "browser_exploration" / "schemas"
    generated = sorted(tmp_path.glob("*.schema.json"))
    assert len(generated) == 5
    assert {path.name for path in generated} == {path.name for path in published.glob("*.json")}
    for path in generated:
        assert json.loads(path.read_text()) == json.loads((published / path.name).read_text())


def test_distribution_excludes_python_playwright_and_host_package() -> None:
    """Verify distribution excludes python playwright and host package.

    Arrange: Prepare the inputs, collaborators, and configuration required for this scenario.
    Act: Exercise the behavior described by the test.
    Assert: Confirm the expected outcome for distribution excludes python playwright and host
            package."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]
    assert all(
        not dependency.startswith(("agentic-qa", "playwright")) for dependency in dependencies
    )
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/browser_exploration"
    ]
