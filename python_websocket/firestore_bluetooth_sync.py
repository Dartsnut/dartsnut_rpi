import threading
import json


class FirestoreBluetoothScanController:
    def __init__(self, scan_builder, timestamp_factory, publish_update):
        self._scan_builder = scan_builder
        self._timestamp_factory = timestamp_factory
        self._publish_update = publish_update
        self._lock = threading.Lock()
        self._in_progress = False
        self._last_list_fingerprint = None

    def start_scan_if_requested(self):
        with self._lock:
            if self._in_progress:
                return False
            self._in_progress = True

        threading.Thread(target=self._scan_worker, daemon=True).start()
        return True

    def _scan_worker(self):
        try:
            bluetooth_list = self._scan_builder()
        except Exception:
            bluetooth_list = []
        finally:
            try:
                list_fingerprint = json.dumps(
                    bluetooth_list, sort_keys=True, separators=(",", ":")
                )
                payload = {"bluetooth": {"is_scan": False}}
                with self._lock:
                    changed = list_fingerprint != self._last_list_fingerprint
                    if changed:
                        self._last_list_fingerprint = list_fingerprint
                if changed:
                    payload["bluetooth"]["list"] = bluetooth_list
                    payload["bluetooth"]["timestamp"] = self._timestamp_factory()
                self._publish_update(payload)
            except Exception:
                pass
            with self._lock:
                self._in_progress = False
