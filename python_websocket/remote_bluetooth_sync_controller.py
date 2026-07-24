import copy
import threading


class RemoteBluetoothScanController:
    def __init__(
        self,
        scan_builder,
        timestamp_factory,
        publish_update,
        connect_device,
        connected_controllers_provider=None,
        remembered_controllers_provider=None,
        on_controller_connected=None,
        on_controller_disconnected=None,
    ):
        self._scan_builder = scan_builder
        self._timestamp_factory = timestamp_factory
        self._publish_update = publish_update
        self._connect_device = connect_device
        self._connected_controllers_provider = (
            connected_controllers_provider or (lambda: [])
        )
        self._remembered_controllers_provider = (
            remembered_controllers_provider or (lambda: [])
        )
        self._on_controller_connected = on_controller_connected
        self._on_controller_disconnected = on_controller_disconnected
        self._lock = threading.Lock()
        self._in_progress = False
        self._connect_in_progress = False
        self._refresh_in_progress = False
        self._observed_connected_macs = None
        self._state = {
            "is_scan": False,
            "controllers": [],
            "scan_results": [],
            "last_scan_at": "",
        }

    def get_state_snapshot(self):
        """Return a detached state copy safe for rendering from another thread."""
        with self._lock:
            return copy.deepcopy(self._state)

    def refresh_remembered_if_requested(self):
        """Refresh paired controller rows without blocking the caller."""
        with self._lock:
            if self._refresh_in_progress:
                return False
            self._refresh_in_progress = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()
        return True

    def poll_connection_status(self):
        """Reconcile paired controller state and notify on BlueZ transitions."""
        remembered = self._load_remembered_controllers()
        if remembered is None:
            return False

        connected_macs = {
            item["mac"]
            for item in remembered
            if item.get("status") == "connected"
        }
        notify_connected = False
        notify_disconnected = False
        payload = None

        with self._lock:
            previous_connected = self._observed_connected_macs
            before = copy.deepcopy(self._state)
            self._merge_remembered_controllers(remembered)

            if previous_connected is not None:
                for mac in previous_connected - connected_macs:
                    self._set_connected_controller_idle(mac)
                notify_connected = bool(connected_macs - previous_connected)
                notify_disconnected = bool(previous_connected - connected_macs)

            self._observed_connected_macs = connected_macs
            if self._state != before:
                payload = {"bluetooth": copy.deepcopy(self._state)}

        if payload is not None:
            try:
                self._publish_update(payload)
            except Exception:
                pass
        if notify_connected:
            self._invoke_callback(self._on_controller_connected)
        if notify_disconnected:
            self._invoke_callback(self._on_controller_disconnected)
        return True

    def apply_explicit_remote_lists(self, bluetooth_cfg):
        """Mirror remote rows into local state when the patch includes explicit keys.

        Remote config may clear ``controllers`` after unpair while this process still
        holds stale entries; without syncing, the next ``start_scan`` publish would
        resurrect removed devices as still paired.
        """
        if not isinstance(bluetooth_cfg, dict):
            return
        with self._lock:
            if "controllers" in bluetooth_cfg:
                raw = bluetooth_cfg.get("controllers")
                lst = raw if isinstance(raw, list) else []
                self._state["controllers"] = self._normalize_scan_entries(lst)
            if "scan_results" in bluetooth_cfg:
                raw = bluetooth_cfg.get("scan_results")
                lst = raw if isinstance(raw, list) else []
                self._state["scan_results"] = self._normalize_scan_entries(lst)

    def start_scan_if_requested(self, sync_connected_controllers=False):
        with self._lock:
            if self._in_progress:
                return False
            self._in_progress = True
            if sync_connected_controllers:
                self._merge_connected_controllers()
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
            connect_name = self._entry_name(src, mac)
            if src == "scan_results":
                # Pairing from discovery: promote to controllers only; drop from scan list.
                self._remove_mac_from_list("scan_results", mac)
                self._upsert_controller(mac, connect_name, "connecting")
            else:
                self._set_status_in_list(src, mac, "connecting")
                self._upsert_controller(mac, connect_name, "connecting")
            payload = {"bluetooth": dict(self._state)}
        self._publish_update(payload)

        threading.Thread(target=self._connect_worker, args=(mac, src), daemon=True).start()
        return True

    def _scan_worker(self):
        remembered = None
        try:
            remembered = self._load_remembered_controllers()
            bluetooth_list = self._normalize_scan_entries(self._scan_builder())
        except Exception:
            bluetooth_list = []
        finally:
            try:
                with self._lock:
                    self._merge_remembered_controllers(remembered)
                    self._state["scan_results"] = bluetooth_list
                    self._state["is_scan"] = False
                    self._state["last_scan_at"] = self._timestamp_factory()
                    payload = {"bluetooth": dict(self._state)}
                self._publish_update(payload)
            except Exception:
                pass
            with self._lock:
                self._in_progress = False

    def _refresh_worker(self):
        try:
            remembered = self._load_remembered_controllers()
            if remembered is None:
                return
            with self._lock:
                self._merge_remembered_controllers(remembered)
                payload = {"bluetooth": copy.deepcopy(self._state)}
            self._publish_update(payload)
        except Exception:
            pass
        finally:
            with self._lock:
                self._refresh_in_progress = False

    def _load_remembered_controllers(self):
        try:
            remembered = self._remembered_controllers_provider()
        except Exception:
            return None
        if not isinstance(remembered, list):
            return None
        return self._normalize_scan_entries(remembered)

    def _merge_remembered_controllers(self, remembered):
        if not isinstance(remembered, list):
            return
        for device in remembered:
            if not isinstance(device, dict):
                continue
            mac = str(device.get("mac") or "").strip().upper()
            if not mac:
                continue
            name = str(device.get("name") or "").strip()
            status = self._normalize_status(device.get("status"))
            existing = next(
                (
                    item
                    for item in (self._state.get("controllers") or [])
                    if item.get("mac") == mac
                ),
                None,
            )
            last_error = ""
            if existing is not None and existing.get("status") in {"connecting", "error"}:
                if status != "connected":
                    status = existing.get("status")
                    last_error = str(existing.get("last_error") or "")
            self._upsert_controller(mac, name, status, last_error)

    def _set_connected_controller_idle(self, mac):
        for item in self._state.get("controllers") or []:
            if item.get("mac") != mac or item.get("status") != "connected":
                continue
            item["status"] = "idle"
            item.pop("last_error", None)
            return

    def _invoke_callback(self, callback):
        if callback is None:
            return
        try:
            callback()
        except Exception:
            pass

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
                if source_list == "scan_results":
                    # Entry was removed from scan_results at pairing start.
                    name = self._entry_name("controllers", address)
                    self._upsert_controller(address, name, next_status, err)
                else:
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

    def _remove_mac_from_list(self, list_key, mac):
        mac_u = str(mac or "").strip().upper()
        if not mac_u:
            return
        items = self._state.get(list_key) or []
        self._state[list_key] = [x for x in items if x.get("mac") != mac_u]

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

    def _merge_connected_controllers(self):
        try:
            os_devices = self._connected_controllers_provider() or []
        except Exception:
            os_devices = []
        if not isinstance(os_devices, list):
            return

        existing_macs = {
            str(item.get("mac") or "").strip().upper()
            for item in (self._state.get("controllers") or [])
            if isinstance(item, dict) and item.get("mac")
        }
        for device in os_devices:
            if not isinstance(device, dict):
                continue
            mac = str(device.get("mac") or device.get("address") or "").strip().upper()
            if not mac or mac in existing_macs:
                continue
            name = str(device.get("name") or "").strip()
            self._upsert_controller(mac, name, "connected")
            existing_macs.add(mac)

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
