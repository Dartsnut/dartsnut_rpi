import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "repair_device_json.sh"


def _run_repair(repo_dir: Path, boot_device_json: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "REPO_DIR": str(repo_dir),
            "BOOT_DEVICE_JSON": str(boot_device_json),
            "WORK_DEVICE_JSON": str(repo_dir / "device.json"),
            "INSTALL_CMD": "install",
        },
        text=True,
        capture_output=True,
    )


def test_repairs_missing_boot_device_json_from_working_copy(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    boot_device_json = tmp_path / "boot" / "device.json"
    (repo_dir / "device.json").write_text(
        '{"serial":"S1","model":"PixelDart","brightness":"80"}\n',
        encoding="utf-8",
    )

    result = _run_repair(repo_dir, boot_device_json)

    assert result.returncode == 0, result.stderr
    assert boot_device_json.read_text(encoding="utf-8") == (
        '{"serial":"S1","model":"PixelDart","brightness":"80"}\n'
    )
    assert "Restoring missing boot device.json" in result.stdout


def test_repairs_missing_working_device_json_from_boot_copy(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    boot_device_json = tmp_path / "boot" / "device.json"
    boot_device_json.parent.mkdir()
    boot_device_json.write_text(
        '{"serial":"S2","model":"PixelBoard","brightness":"100"}\n',
        encoding="utf-8",
    )

    result = _run_repair(repo_dir, boot_device_json)

    assert result.returncode == 0, result.stderr
    assert (repo_dir / "device.json").read_text(encoding="utf-8") == (
        '{"serial":"S2","model":"PixelBoard","brightness":"100"}\n'
    )
    assert "Restoring missing runtime device.json" in result.stdout


def test_device_json_repair_is_noop_when_both_missing(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    boot_device_json = tmp_path / "boot" / "device.json"

    result = _run_repair(repo_dir, boot_device_json)

    assert result.returncode == 0, result.stderr
    assert not boot_device_json.exists()
    assert not (repo_dir / "device.json").exists()
    assert "Warning: device.json missing in both locations" in result.stdout
