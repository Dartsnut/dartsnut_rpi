import threading


class RemoteBluetoothScanController:
    def __init__(self, scan_builder, timestamp_factory, publish_update, connect_device):
        self._scan_builder = scan_builder
        self._timestamp_factory = timestamp_factory
        self._publish_update = publish_update
        self._connect_device = connect_device
        self._lock = threading.Lock()
        self._in_progress = False
        self._connect_in_progress = False
        self._state = {
            "is_scan": False,
            "controllers": [],
            "scan_results": [],
            "last_scan_at": "",
        }

    def start_scan_if_requested(self):
        with self._lock:
            if self._in_progress:
                return False
            self._in_progress = True
            # Clear stale scan results immediately when a new scan starts.
            self._state["is_scan"] = True
            self._state["scan_results"] = []
            payload = {"bluetooth": dict(self._state)}
        self._publish_update(payload)

        threading.Thread(target=self._scan_worker, daemon=True).start()
        return True

    def start_connect_if_requested(self, address, source_list="scan_results"):
        mac = str(address or "").strip().upper()
        if not mac:
            return False
        src = str(source_list or "scan_results").strip().lower()
        if src not in {"scan_results", "controllers"}:
            src = "scan_results"
        with self._lock:
            if self._connect_in_progress:
                return False
            self._connect_in_progress = True
            self._set_status_in_list(src, mac, "connecting")
            self._upsert_controller(mac, "", "connecting")
            payload = {"bluetooth": dict(self._state)}
        self._publish_update(payload)

        threading.Thread(target=self._connect_worker, args=(mac, src), daemon=True).start()
        return True

    def _scan_worker(self):
        try:
            bluetooth_list = self._normalize_scan_entries(self._scan_builder())
        except Exception:
            bluetooth_list = []
        finally:
            try:
                with self._lock:
                    self._state["scan_results"] = bluetooth_list
                    self._state["is_scan"] = False
                    self._state["last_scan_at"] = self._timestamp_factory()
                    payload = {"bluetooth": dict(self._state)}
                self._publish_update(payload)
            except Exception:
                pass
            with self._lock:
                self._in_progress = False

    def _connect_worker(self, address, source_list):
        try:
            success, error_message = self._connect_device(address)
        except Exception:
            success = False
            error_message = "Connection failed"

        try:
            with self._lock:
                next_status = "connected" if success else "error"
                err = "" if success else (error_message or "Connection failed")
                self._set_status_in_list(source_list, address, next_status, err)
                name = self._entry_name(source_list, address)
                self._upsert_controller(address, name, next_status, err)
                payload = {"bluetooth": dict(self._state)}
            self._publish_update(payload)
        except Exception:
            pass
        finally:
            with self._lock:
                self._connect_in_progress = False

    def _normalize_scan_entries(self, bluetooth_list):
        if not isinstance(bluetooth_list, list):
            return []
        out = []
        seen = set()
        for item in bluetooth_list:
            if not isinstance(item, dict):
                continue
            mac = str(item.get("mac") or item.get("address") or "").strip().upper()
            if not mac or mac in seen:
                continue
            seen.add(mac)
            out.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "mac": mac,
                    "status": self._normalize_status(item.get("status")),
                }
            )
        return out

    def _normalize_status(self, status):
        value = str(status or "").strip().lower()
        if value in {"idle", "connecting", "connected", "error"}:
            return value
        if value == "connect":
            return "connecting"
        if value == "disconnected":
            return "idle"
        return "idle"

    def _entry_name(self, source_list, mac):
        items = self._state.get(source_list) or []
        for item in items:
            if item.get("mac") == mac:
                return str(item.get("name") or "").strip()
        return ""

    def _set_status_in_list(self, source_list, mac, status, last_error=""):
        items = self._state.get(source_list) or []
        for item in items:
            if item.get("mac") == mac:
                item["status"] = self._normalize_status(status)
                if last_error:
                    item["last_error"] = last_error
                elif "last_error" in item:
                    del item["last_error"]
                return
        new_entry = {
            "name": "",
            "mac": mac,
            "status": self._normalize_status(status),
        }
        if last_error:
            new_entry["last_error"] = last_error
        items.append(new_entry)
        self._state[source_list] = items

    def _upsert_controller(self, mac, name, status, last_error=""):
        items = self._state.get("controllers") or []
        for item in items:
            if item.get("mac") == mac:
                if name and not item.get("name"):
                    item["name"] = name
                item["status"] = self._normalize_status(status)
                if last_error:
                    item["last_error"] = last_error
                elif "last_error" in item:
                    del item["last_error"]
                return
        entry = {
            "name": str(name or "").strip(),
            "mac": mac,
            "status": self._normalize_status(status),
        }
        if last_error:
            entry["last_error"] = last_error
        items.append(entry)
        self._state["controllers"] = items
