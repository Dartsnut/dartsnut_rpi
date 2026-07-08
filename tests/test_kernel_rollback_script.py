import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "rollback_rpi_kernel_6_12.sh"


def _write_stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def test_kernel_rollback_skips_non_618_kernel(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    firmware = tmp_path / "firmware"
    boot.mkdir()
    firmware.mkdir()

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "DARTSNUT_CURRENT_KERNEL": "6.12.47+rpt-rpi-v8",
            "DARTSNUT_BOOT_DIR": str(boot),
            "DARTSNUT_FIRMWARE_DIR": str(firmware),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "Kernel rollback not needed" in result.stdout


def test_kernel_rollback_installs_versioned_kernel_updates_firmware_files_and_reboots(
    tmp_path: Path,
) -> None:
    boot = tmp_path / "boot"
    firmware = tmp_path / "firmware"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "commands.log"
    boot.mkdir()
    firmware.mkdir()
    bin_dir.mkdir()
    (boot / "vmlinuz-6.12.47+rpt-rpi-v8").write_text("kernel-6.12", encoding="utf-8")
    (boot / "initrd.img-6.12.47+rpt-rpi-v8").write_text("initrd-6.12", encoding="utf-8")
    (firmware / "kernel8.img").write_text("kernel-6.18", encoding="utf-8")
    (firmware / "initramfs8").write_text("initrd-6.18", encoding="utf-8")

    _write_stub(
        bin_dir,
        "apt-get",
        'printf "apt-get %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "apt-cache",
        'printf "apt-cache %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "apt-mark",
        'printf "apt-mark %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "dpkg-query",
        'printf "install ok installed"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "sync",
        'printf "sync\\n" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "reboot",
        'printf "reboot\\n" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DARTSNUT_TEST_LOG": str(log),
            "DARTSNUT_KERNEL_ROLLBACK_NO_SUDO": "1",
            "DARTSNUT_CURRENT_KERNEL": "6.18.34+rpt-rpi-v8",
            "DARTSNUT_BOOT_DIR": str(boot),
            "DARTSNUT_FIRMWARE_DIR": str(firmware),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert (firmware / "kernel8.img").read_text(encoding="utf-8") == "kernel-6.12"
    assert (firmware / "initramfs8").read_text(encoding="utf-8") == "initrd-6.12"
    assert (firmware / "kernel8.img.6.18.34+rpt-rpi-v8.bak").read_text(
        encoding="utf-8"
    ) == "kernel-6.18"
    assert (firmware / "initramfs8.6.18.34+rpt-rpi-v8.bak").read_text(
        encoding="utf-8"
    ) == "initrd-6.18"
    assert log.read_text(encoding="utf-8").splitlines() == [
        "apt-get update",
        "apt-get install -y --allow-downgrades linux-image-6.12.47+rpt-rpi-v8=1:6.12.47-1+rpt1",
        "apt-cache show linux-headers-6.12.47+rpt-rpi-v8=1:6.12.47-1+rpt1",
        "apt-get install -y --allow-downgrades linux-headers-6.12.47+rpt-rpi-v8=1:6.12.47-1+rpt1",
        "apt-mark hold linux-image-rpi-v8",
        "apt-mark hold linux-headers-rpi-v8",
        "apt-mark hold linux-image-rpi-2712",
        "apt-mark hold linux-headers-rpi-2712",
        "apt-mark hold linux-image-6.12.47+rpt-rpi-v8",
        "apt-mark hold linux-headers-6.12.47+rpt-rpi-v8",
        "sync",
        "reboot",
    ]


def test_kernel_rollback_can_defer_reboot_after_staging(tmp_path: Path) -> None:
    boot = tmp_path / "boot"
    firmware = tmp_path / "firmware"
    bin_dir = tmp_path / "bin"
    log = tmp_path / "commands.log"
    boot.mkdir()
    firmware.mkdir()
    bin_dir.mkdir()
    (boot / "vmlinuz-6.12.47+rpt-rpi-v8").write_text("kernel-6.12", encoding="utf-8")
    (boot / "initrd.img-6.12.47+rpt-rpi-v8").write_text("initrd-6.12", encoding="utf-8")
    (firmware / "kernel8.img").write_text("kernel-6.18", encoding="utf-8")
    (firmware / "initramfs8").write_text("initrd-6.18", encoding="utf-8")

    _write_stub(
        bin_dir,
        "apt-get",
        'printf "apt-get %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "apt-cache",
        'printf "apt-cache %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "apt-mark",
        'printf "apt-mark %s\\n" "$*" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "dpkg-query",
        'printf "install ok installed"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "sync",
        'printf "sync\\n" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )
    _write_stub(
        bin_dir,
        "reboot",
        'printf "reboot\\n" >> "$DARTSNUT_TEST_LOG"\nexit 0\n',
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DARTSNUT_TEST_LOG": str(log),
            "DARTSNUT_KERNEL_ROLLBACK_NO_SUDO": "1",
            "DARTSNUT_KERNEL_ROLLBACK_DEFER_REBOOT": "1",
            "DARTSNUT_CURRENT_KERNEL": "6.18.34+rpt-rpi-v8",
            "DARTSNUT_BOOT_DIR": str(boot),
            "DARTSNUT_FIRMWARE_DIR": str(firmware),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 77
    assert (firmware / "kernel8.img").read_text(encoding="utf-8") == "kernel-6.12"
    assert "Kernel rollback staged. Reboot deferred to caller." in result.stdout
    assert log.read_text(encoding="utf-8").splitlines()[-1] == "sync"
    assert "reboot" not in log.read_text(encoding="utf-8").splitlines()
