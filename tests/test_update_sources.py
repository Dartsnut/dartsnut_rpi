import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import update_sources


def _cp(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_parse_ping_average_linux_summary():
    output = (
        "--- github.com ping statistics ---\n"
        "3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n"
        "rtt min/avg/max/mdev = 10.100/20.250/30.900/8.500 ms\n"
    )
    assert update_sources.parse_ping_average(output) == 20.25


def test_parse_ping_average_rejects_malformed_output():
    assert update_sources.parse_ping_average("3 packets transmitted") is None


def test_ping_average_uses_three_samples_and_c_locale(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _cp("round-trip min/avg/max/stddev = 1.0/2.5/4.0/1.0 ms\n")

    monkeypatch.setattr(update_sources.subprocess, "run", fake_run)

    assert update_sources.ping_average("example.com") == 2.5
    cmd, kwargs = calls[0]
    assert cmd == ["ping", "-c", "3", "-W", "2", "example.com"]
    assert kwargs["env"]["LC_ALL"] == "C"
    assert kwargs["timeout"] == 10


def test_choose_by_latency_prefers_faster_and_direct_on_ties(monkeypatch):
    latencies = {"direct": 20.0, "mirror": 10.0}
    monkeypatch.setattr(update_sources, "ping_average", latencies.get)
    assert update_sources.choose_by_latency("direct", "mirror", label="test") == "mirror"

    latencies["mirror"] = 20.0
    assert update_sources.choose_by_latency("direct", "mirror", label="test") == "direct"


def test_choose_by_latency_uses_only_reachable_host(monkeypatch):
    monkeypatch.setattr(
        update_sources,
        "ping_average",
        lambda host: None if host == "direct" else 12.0,
    )
    assert update_sources.choose_by_latency("direct", "mirror", label="test") == "mirror"

    monkeypatch.setattr(update_sources, "ping_average", lambda _host: None)
    assert update_sources.choose_by_latency("direct", "mirror", label="test") == "direct"


def test_ping_average_missing_ping_falls_back(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError("ping")

    monkeypatch.setattr(update_sources.subprocess, "run", fake_run)
    assert update_sources.ping_average("example.com") is None


def test_is_https_github_origin():
    assert update_sources.is_https_github_origin(
        "https://github.com/Dartsnut/dartsnut_rpi.git"
    )
    assert update_sources.is_https_github_origin(
        "https://token@github.com/Dartsnut/dartsnut_rpi.git"
    )
    assert update_sources.is_https_github_origin(
        "https://v4.gh-proxy.org/https://github.com/Dartsnut/dartsnut_rpi.git"
    )
    assert not update_sources.is_https_github_origin(
        "https://git.example.com/Dartsnut/dartsnut_rpi.git"
    )
    assert not update_sources.is_https_github_origin(
        "git@github.com:Dartsnut/dartsnut_rpi.git"
    )
    assert not update_sources.is_https_github_origin(
        "https://github.com.example/Dartsnut/dartsnut_rpi.git"
    )


def test_prepare_git_source_skips_ping_for_non_github_and_removes_rewrite(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _cp("https://git.example.com/Dartsnut/dartsnut_rpi.git\n")
        if cmd[:4] == ["git", "config", "--global", "--unset-all"]:
            return _cp(returncode=5)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(update_sources.subprocess, "run", fake_run)

    assert update_sources.prepare_git_source("/repo") == "direct"
    assert not any(cmd and cmd[0] == "ping" for cmd in calls)
    assert calls[-1] == [
        "git",
        "config",
        "--global",
        "--unset-all",
        update_sources.GITHUB_REWRITE_KEY,
        "https://",
    ]


def test_prepare_git_source_enables_proxy_for_faster_mirror(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _cp("https://github.com/Dartsnut/dartsnut_rpi.git\n")
        if cmd[:4] == ["git", "config", "--global", "--replace-all"]:
            return _cp()
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(update_sources.subprocess, "run", fake_run)
    monkeypatch.setattr(
        update_sources,
        "choose_by_latency",
        lambda direct, mirror, label: "mirror",
    )

    assert update_sources.prepare_git_source("/repo") == "mirror"
    assert calls[-1] == [
        "git",
        "config",
        "--global",
        "--replace-all",
        update_sources.GITHUB_REWRITE_KEY,
        "https://",
    ]


def test_prepare_git_source_config_failure_falls_back_direct(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _cp("https://github.com/Dartsnut/dartsnut_rpi.git\n")
        if cmd[:4] == ["git", "config", "--global", "--replace-all"]:
            return _cp(stderr="permission denied", returncode=1)
        if cmd[:4] == ["git", "config", "--global", "--unset-all"]:
            return _cp(returncode=5)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(update_sources.subprocess, "run", fake_run)
    monkeypatch.setattr(
        update_sources,
        "choose_by_latency",
        lambda direct, mirror, label: "mirror",
    )

    assert update_sources.prepare_git_source("/repo") == "direct"
    assert any(cmd[:4] == ["git", "config", "--global", "--unset-all"] for cmd in calls)


def test_select_uv_index_uses_ustc_when_faster(monkeypatch):
    monkeypatch.setattr(
        update_sources,
        "choose_by_latency",
        lambda direct, mirror, label: "mirror",
    )
    assert update_sources.select_uv_index() == update_sources.USTC_PYPI_INDEX


def test_prepare_update_sources_never_raises(monkeypatch):
    monkeypatch.setattr(
        update_sources,
        "prepare_git_source",
        lambda _cwd: (_ for _ in ()).throw(RuntimeError("git failed")),
    )
    monkeypatch.setattr(
        update_sources,
        "select_uv_index",
        lambda: (_ for _ in ()).throw(RuntimeError("uv failed")),
    )
    monkeypatch.setattr(update_sources, "_remove_managed_git_rewrite", lambda: True)

    assert update_sources.prepare_update_sources("/repo") == update_sources.DIRECT_PYPI_INDEX
