from __future__ import annotations

import subprocess

from python_websocket import wifi_operations


def test_parse_wifi_scan_output_unescapes_dedupes_and_sorts():
    output = "\n".join(
        [
            r":Cafe\:Guest:42:--",
            r"*:家庭网络:90:WPA2",
            r":Cafe\:Guest:75:WPA2",
            r"*:家庭网络:80:WPA2",
            r"::99:WPA2",
            r":Weak:12:WEP",
        ]
    )

    assert wifi_operations.parse_wifi_scan_output(output) == [
        {
            "ssid": "家庭网络", "rssi": 90, "security": "WPA2",
            "secured": True, "connected": True,
        },
        {
            "ssid": "Cafe:Guest", "rssi": 75, "security": "WPA2",
            "secured": True, "connected": False,
        },
        {
            "ssid": "Weak", "rssi": 12, "security": "WEP",
            "secured": True, "connected": False,
        },
    ]


def test_parse_wifi_scan_output_marks_open_network():
    assert wifi_operations.parse_wifi_scan_output(":Open:70:--") == [
        {
            "ssid": "Open", "rssi": 70, "security": "--",
            "secured": False, "connected": False,
        }
    ]


def test_connect_wifi_network_uses_password_only_for_secured(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="connected\n", stderr="")

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    wifi_operations.connect_wifi_network("Secure", "secret", True)
    wifi_operations.connect_wifi_network("Open", "", False)

    assert calls[0][0] == [
        "nmcli", "dev", "wifi", "connect", "Secure", "password", "secret"
    ]
    assert calls[1][0] == ["nmcli", "dev", "wifi", "connect", "Open"]
    assert all(call[1]["check"] is True for call in calls)


def test_scan_wifi_networks_rescans_before_listing(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-1] == "list":
            stdout = "*:Home:88:WPA2\n"
        elif command[-2:] == ["connection", "show"]:
            stdout = (
                "Home Profile:home-uuid:802-11-wireless\n"
                "Wired:wired-uuid:802-3-ethernet\n"
            )
        elif command[-3:] == ["show", "uuid", "home-uuid"]:
            stdout = "Home\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    assert wifi_operations.scan_wifi_networks() == [
        {
            "ssid": "Home", "rssi": 88, "security": "WPA2",
            "secured": True, "connected": True,
        },
        {
            "ssid": "Home", "profile": "Home Profile",
            "remembered": True, "connected": False,
        },
    ]
    assert calls == [
        ["nmcli", "dev", "wifi", "rescan"],
        [
            "nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
            "dev", "wifi", "list",
        ],
        ["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
        [
            "nmcli", "-g", "802-11-wireless.ssid",
            "connection", "show", "uuid", "home-uuid",
        ],
    ]


def test_list_saved_wifi_profiles_queries_ssid_per_wifi_profile(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["check"]))
        if command[-2:] == ["connection", "show"]:
            stdout = "\n".join(
                [
                    r"Cafe\:Profile:cafe-uuid:802-11-wireless",
                    r"家庭网络:home-uuid:802-11-wireless",
                    r"Wired:wired-uuid:802-3-ethernet",
                ]
            )
            return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")
        if command[-1] == "cafe-uuid":
            return subprocess.CompletedProcess(
                command, 0, stdout=r"Cafe\:Guest" + "\n", stderr=""
            )
        if command[-1] == "home-uuid":
            return subprocess.CompletedProcess(
                command, 0, stdout="家庭网络\n", stderr=""
            )
        raise AssertionError(command)

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    assert wifi_operations.list_saved_wifi_profiles() == [
        {
            "ssid": "Cafe:Guest", "profile": "Cafe:Profile",
            "remembered": True, "connected": False,
        },
        {
            "ssid": "家庭网络", "profile": "家庭网络",
            "remembered": True, "connected": False,
        },
    ]
    assert calls == [
        (["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"], True),
        ([
            "nmcli", "-g", "802-11-wireless.ssid",
            "connection", "show", "uuid", "cafe-uuid",
        ], False),
        ([
            "nmcli", "-g", "802-11-wireless.ssid",
            "connection", "show", "uuid", "home-uuid",
        ], False),
    ]


def test_list_saved_wifi_profiles_tolerates_summary_failure(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(2, command, stderr="unsupported field")

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    assert wifi_operations.list_saved_wifi_profiles() == []


def test_list_saved_wifi_profiles_skips_profile_detail_errors(monkeypatch):
    def fake_run(command, **kwargs):
        if command[-2:] == ["connection", "show"]:
            return subprocess.CompletedProcess(
                command, 0,
                stdout="Broken:broken-uuid:802-11-wireless\n", stderr="",
            )
        return subprocess.CompletedProcess(command, 10, stdout="", stderr="gone")

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    assert wifi_operations.list_saved_wifi_profiles() == []


def test_parse_saved_wifi_profiles_handles_escaped_unicode_and_dedupes():
    output = "\n".join(
        [
            r"Home Profile:802-11-wireless:Home",
            r"Cafe\:Saved:802-11-wireless:Cafe\:Guest",
            r"家庭网络:802-11-wireless:家庭网络",
            r"Duplicate:802-11-wireless:Home",
            r"Wired:802-3-ethernet:",
        ]
    )

    assert wifi_operations.parse_saved_wifi_profiles(output) == [
        {
            "ssid": "Cafe:Guest", "profile": "Cafe:Saved",
            "remembered": True, "connected": False,
        },
        {
            "ssid": "Home", "profile": "Home Profile",
            "remembered": True, "connected": False,
        },
        {
            "ssid": "家庭网络", "profile": "家庭网络",
            "remembered": True, "connected": False,
        },
    ]


def test_saved_wifi_connect_and_forget_use_profile_id(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(wifi_operations.subprocess, "run", fake_run)

    wifi_operations.connect_saved_wifi("Home Profile")
    wifi_operations.forget_saved_wifi("Home Profile")

    assert calls == [
        ["nmcli", "connection", "up", "id", "Home Profile"],
        ["nmcli", "connection", "delete", "id", "Home Profile"],
    ]
