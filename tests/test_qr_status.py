from runtime.qr_status import read_qr_status, write_qr_status


def test_qr_status_round_trip_and_missing_default(tmp_path):
    path = tmp_path / "qr.json"
    assert read_qr_status(path=str(path)) == {
        "supabase_connected": False,
    }

    write_qr_status(True, path=str(path))

    assert read_qr_status(path=str(path)) == {
        "supabase_connected": True,
    }
