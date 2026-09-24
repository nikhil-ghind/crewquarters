"""`crewctl init`: create a new agent directory from templates."""

from __future__ import annotations

import re
from pathlib import Path

from crewctl import templates
from crewctl.build import find_repo_root

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class ScaffoldError(Exception):
    pass


def scaffold(name: str, target: Path) -> list[Path]:
    if not NAME_RE.match(name):
        raise ScaffoldError("agent name must be 2-63 characters: lowercase letters, digits, and hyphens")
    if target.exists() and any(target.iterdir()):
        raise ScaffoldError(f"{target} is not empty")
    module = name.replace("-", "_")
    title = name.replace("-", " ").title()
    try:
        agent_path = target.resolve().relative_to(find_repo_root(target.resolve().parent)).as_posix()
    except (FileNotFoundError, ValueError):
        agent_path = f"agents/{name}"
    values = {
        "agent_id": name,
        "module": module,
        "title": title,
        "agent_path": agent_path,
        "base_digest": templates.PYTHON_BASE_DIGEST,
    }
    files = {
        "manifest.yaml": templates.MANIFEST.substitute(values),
        "pyproject.toml": templates.PYPROJECT.substitute(values),
        "Dockerfile": templates.DOCKERFILE.substitute(values),
        "README.md": templates.README.substitute(values),
        f"src/{module}/__init__.py": "",
        f"src/{module}/__main__.py": templates.MAIN.substitute(values),
        "tests/test_agent.py": templates.TEST.substitute(values),
        "scenarios/default/scenario.yaml": templates.SCENARIO,
        "scenarios/default/config.yaml": templates.CONFIG,
    }
    written = []
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
