import json
import socket
import tarfile
import threading

import supabase_command_worker as worker


def test_run_command_success_captures_stdout(tmp_path):
    result = worker.run_command("printf hello", cwd=tmp_path, timeout_seconds=20)

    assert result.status_code == 0
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.timed_out is False


def test_run_command_failure_captures_stderr(tmp_path):
    result = worker.run_command(
        "printf nope >&2; exit 7",
        cwd=tmp_path,
        timeout_seconds=20,
    )

    assert result.status_code == 7
    assert result.stdout == ""
    assert result.stderr == "nope"
    assert result.timed_out is False


def test_run_command_timeout_kills_process_group(tmp_path):
    result = worker.run_command("sleep 5", cwd=tmp_path, timeout_seconds=0.1)

    assert result.status_code == 124
    assert result.timed_out is True


def test_write_log_archive_contains_command_metadata(tmp_path):
    command_result = worker.CommandResult(
        command="printf hello",
        status_code=0,
        stdout="hello",
        stderr="",
        timed_out=False,
        started_at="2026-07-02T00:00:00+00:00",
        finished_at="2026-07-02T00:00:01+00:00",
    )

    archive = worker.write_log_archive(
        command_result,
        log_dir=tmp_path,
        command_id="cmd-1",
        device_id="AA:BB:CC:DD:EE:FF",
    )

    assert archive.name.endswith(".tar.gz")
    with tarfile.open(archive.path, "r:gz") as tar:
        names = tar.getnames()
        assert names == [archive.log_name]
        content = tar.extractfile(names[0]).read().decode("utf-8")
    assert "printf hello" in content
    assert '"status_code": 0' in content
    assert "hello" in content


def test_upload_archive_posts_multipart_file(tmp_path, monkeypatch):
    archive_path = tmp_path / "command.tar.gz"
    archive_path.write_bytes(b"archive")
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

    def fake_post(url, files, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        file_name, file_obj, mime = files["file"]
        captured["file_name"] = file_name
        captured["file_bytes"] = file_obj.read()
        captured["mime"] = mime
        return _Response()

    monkeypatch.setattr(worker.requests, "post", fake_post)

    worker.upload_archive(archive_path, "https://api.dartsnut.com/xxx")

    assert captured == {
        "url": "https://api.dartsnut.com/xxx",
        "timeout": 30,
        "file_name": "command.tar.gz",
        "file_bytes": b"archive",
        "mime": "application/gzip",
    }


def test_socket_request_response_frame(tmp_path, monkeypatch):
    responses = []

    def handler(payload):
        responses.append(payload)
        return {"kind": "command_result", "payload": {"status_code": 0, "log_filename": "x.tar.gz"}}

    server = worker.CommandSocketServer("unused.sock", handler=handler)
    client, server_conn = socket.socketpair()
    thread = threading.Thread(target=server._serve_connection, args=(server_conn,), daemon=True)
    thread.start()

    with client:
        client.sendall(
            json.dumps(
                {
                    "kind": "run_command",
                    "payload": {"command_id": "cmd-1", "command": "printf hi"},
                }
            ).encode("utf-8")
            + b"\n"
        )
        line = client.recv(4096).split(b"\n", 1)[0]

    server_conn.close()
    thread.join(timeout=2)

    assert responses == [{"command_id": "cmd-1", "command": "printf hi"}]
    assert json.loads(line.decode("utf-8")) == {
        "kind": "command_result",
        "payload": {"status_code": 0, "log_filename": "x.tar.gz"},
    }
