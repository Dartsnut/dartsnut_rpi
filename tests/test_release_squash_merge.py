import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "dev_scripts" / "release_squash_merge.sh"


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    )


def write_file(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_all(repo: Path, message: str) -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-m", message)


def test_dry_run_ignores_always_stripped_folders(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "master")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")

    dev_scripts_dir = repo / "dev_scripts"
    dev_scripts_dir.mkdir()
    shutil.copy2(SCRIPT, dev_scripts_dir / "release_squash_merge.sh")
    write_file(repo / "README.md")
    commit_all(repo, "initial")
    run_git(repo, "branch", "release")
    run_git(repo, "update-ref", "refs/remotes/origin/release", "release")

    for ignored_path in (
        "docs/release.md",
        "openspec/spec.md",
        "dev_scripts/dev_only.sh",
        "scripts/release_helper.sh",
        "supabase/migrations/001.sql",
        "supabase_bridge/src/main.rs",
        "tests/test_release.py",
    ):
        write_file(repo / ignored_path)
    write_file(repo / "unexpected_device_file.py")
    commit_all(repo, "add dry-run candidates")

    result = subprocess.run(
        ["bash", "dev_scripts/release_squash_merge.sh", "--dry-run", "v1.2.3"],
        cwd=repo,
        env={**os.environ, "SKIP_GIT_FETCH": "1"},
        check=True,
        text=True,
        capture_output=True,
    )

    assert "unexpected_device_file.py" in result.stderr
    for ignored_path in (
        "docs/release.md",
        "openspec/spec.md",
        "dev_scripts/dev_only.sh",
        "scripts/release_helper.sh",
        "supabase/migrations/001.sql",
        "supabase_bridge/src/main.rs",
        "tests/test_release.py",
    ):
        assert ignored_path not in result.stderr
