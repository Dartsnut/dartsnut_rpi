from runtime.reset_workflow import request_confirm_and_forget_wifi


class _EventStub:
    def __init__(self, wait_result: bool):
        self.wait_result = wait_result
        self.clear_calls = 0
        self.wait_calls = []

    def clear(self):
        self.clear_calls += 1

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.wait_result


def test_request_confirm_and_forget_wifi_confirmed_path():
    calls = []
    logs = []
    event = _EventStub(True)

    def _request():
        calls.append("request")

    def _forget():
        calls.append("forget")

    confirmed = request_confirm_and_forget_wifi(
        request_device_reset_state=_request,
        confirm_event=event,
        confirm_timeout_seconds=10.0,
        forget_wifi=_forget,
        log=logs.append,
    )

    assert confirmed is True
    assert calls == ["request", "forget"]
    assert event.clear_calls == 1
    assert event.wait_calls == [10.0]
    assert "Reset: remote confirmation received" in logs


def test_request_confirm_and_forget_wifi_timeout_still_forgets_wifi():
    calls = []
    logs = []
    event = _EventStub(False)

    def _request():
        calls.append("request")

    def _forget():
        calls.append("forget")

    confirmed = request_confirm_and_forget_wifi(
        request_device_reset_state=_request,
        confirm_event=event,
        confirm_timeout_seconds=10.0,
        forget_wifi=_forget,
        log=logs.append,
    )

    assert confirmed is False
    assert calls == ["request", "forget"]
    assert event.clear_calls == 1
    assert event.wait_calls == [10.0]
    assert any("timeout" in msg for msg in logs)
