import subprocess


def get_wifi_ipv4(interface: str = "wlan0", fallback: str = "0.0.0.0") -> str:
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", interface],
            capture_output=True,
            text=True,
            check=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if "inet" in parts:
                inet_idx = parts.index("inet")
                if inet_idx + 1 < len(parts):
                    return parts[inet_idx + 1].split("/")[0]
    except Exception:
        pass
    return fallback
