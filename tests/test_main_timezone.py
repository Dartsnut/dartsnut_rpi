import ast
import pathlib
import subprocess
import time
from types import SimpleNamespace


def _load_set_time_zone():
    source = pathlib.Path(__file__).parents[1].joinpath("main.py").read_text()
    module = ast.parse(source)
    function = next(
        node for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "set_time_zone"
    )
    namespace = {
        "subprocess": subprocess,
        "time": time,
        "_log": SimpleNamespace(error=lambda *_args: None),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace["set_time_zone"]


def test_set_time_zone_refreshes_process_timezone_after_system_change(monkeypatch):
    set_time_zone = _load_set_time_zone()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["timedatectl", "show"]:
            return SimpleNamespace(stdout="Asia/Shanghai\n")
        return SimpleNamespace(stdout="")

    tzset_calls = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "tzset", lambda: tzset_calls.append(True))

    set_time_zone("Europe/Paris")

    assert calls == [
        ["timedatectl", "show", "--property=Timezone", "--value"],
        ["sudo", "timedatectl", "set-timezone", "Europe/Paris"],
    ]
    assert tzset_calls == [True]


def test_set_time_zone_does_not_refresh_when_unchanged_or_failed(monkeypatch):
    set_time_zone = _load_set_time_zone()
    tzset_calls = []
    monkeypatch.setattr(time, "tzset", lambda: tzset_calls.append(True))

    def unchanged_run(command, **kwargs):
        return SimpleNamespace(stdout="Europe/Paris\n")

    monkeypatch.setattr(subprocess, "run", unchanged_run)
    set_time_zone("Europe/Paris")
    assert tzset_calls == []

    def failed_run(command, **kwargs):
        if command[:2] == ["timedatectl", "show"]:
            return SimpleNamespace(stdout="Asia/Shanghai\n")
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", failed_run)
    set_time_zone("Europe/Paris")
    assert tzset_calls == []
