import pytest

from core.retry import retry_with_backoff


def test_retry_returns_first_success_without_sleeping():
    slept = []
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return "ok"

    result = retry_with_backoff(
        _fn, backoff=(1.0, 2.0), sleep=slept.append, label="t"
    )
    assert result == "ok"
    assert calls["n"] == 1
    assert slept == []


def test_retry_succeeds_after_failures():
    slept = []
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return calls["n"] >= 3  # fail twice (False), then succeed

    result = retry_with_backoff(
        _fn, backoff=(1.0, 2.0, 4.0), sleep=slept.append, label="t"
    )
    assert result is True
    assert calls["n"] == 3
    assert slept == [1.0, 2.0]  # slept before attempts 2 and 3, not after success


def test_retry_exhaustion_returns_last_falsy():
    slept = []
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return False

    result = retry_with_backoff(
        _fn, backoff=(1.0, 2.0), sleep=slept.append, label="t"
    )
    assert result is False
    assert calls["n"] == 3  # len(backoff) + 1
    assert slept == [1.0, 2.0]


def test_retry_swallows_exception_when_not_reraise():
    def _fn():
        raise ValueError("boom")

    result = retry_with_backoff(
        _fn, backoff=(0.0,), sleep=lambda _s: None, label="t"
    )
    assert result is None


def test_retry_reraises_last_exception():
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        raise ValueError(f"boom {calls['n']}")

    with pytest.raises(ValueError, match="boom 3"):
        retry_with_backoff(
            _fn,
            backoff=(0.0, 0.0),
            sleep=lambda _s: None,
            reraise=True,
            label="t",
        )
    assert calls["n"] == 3


def test_retry_recovers_from_exception_then_success():
    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    result = retry_with_backoff(
        _fn, backoff=(0.0, 0.0), sleep=lambda _s: None, label="t"
    )
    assert result == "ok"
    assert calls["n"] == 2
