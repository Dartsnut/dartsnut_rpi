from python_websocket import device_operations as devops


def test_forget_wifi_disconnects_device_and_disables_autoconnect(monkeypatch):
    calls = []

    def _check_output(cmd, text=True):
        calls.append(("check_output", cmd))
        if cmd == ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]:
            return "HomeWifi:802-11-wireless\nWired connection 1:802-3-ethernet\n"
        return ""

    def _run(cmd, check=False, capture_output=False, text=False):
        calls.append(("run", cmd))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(devops.subprocess, "check_output", _check_output)
    monkeypatch.setattr(devops.subprocess, "run", _run)

    devops.forget_wifi()

    run_cmds = [cmd for kind, cmd in calls if kind == "run"]

    assert ["nmcli", "connection", "modify", "HomeWifi", "connection.autoconnect", "no"] in run_cmds
    assert ["nmcli", "connection", "delete", "HomeWifi"] in run_cmds
    assert ["nmcli", "device", "disconnect", "wlan0"] in run_cmds
