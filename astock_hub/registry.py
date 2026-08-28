from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "astock_hub.json"


@dataclass(frozen=True)
class ProjectStatus:
    project_id: str
    role: str
    path: Path
    exists: bool
    is_git_repo: bool
    branch: str
    dirty: bool
    license_name: str
    integration: str


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _git_output(repo: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode, result.stdout.strip()


def inspect_projects(config: dict[str, Any], root: Path = PROJECT_ROOT) -> list[ProjectStatus]:
    statuses: list[ProjectStatus] = []
    for item in config["projects"]:
        path = root / item["path"]
        exists = path.is_dir()
        is_git_repo = exists and (path / ".git").exists()
        branch = ""
        dirty = False
        if is_git_repo:
            code, branch = _git_output(path, "branch", "--show-current")
            if code != 0:
                branch = "unknown"
            code, changes = _git_output(path, "status", "--porcelain")
            dirty = code == 0 and bool(changes)
        statuses.append(
            ProjectStatus(
                project_id=item["id"],
                role=item["role"],
                path=path,
                exists=exists,
                is_git_repo=is_git_repo,
                branch=branch,
                dirty=dirty,
                license_name=item["license"],
                integration=item["integration"],
            )
        )
    return statuses
