use anyhow::{Context, Result};
use chrono::Utc;
use flate2::write::GzEncoder;
use flate2::Compression;
use reqwest::blocking::{multipart, Client};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs::{self, File};
use std::io::{ErrorKind, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use tar::Builder;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message};
use url::Url;

const EMBEDDED_SUPABASE_URL: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_URL");
const EMBEDDED_SUPABASE_KEY: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_KEY");
const DEFAULT_UPLOAD_URL: &str = "https://api.dartsnut.com/v1/mobile/device-log/upload";
const DEFAULT_TIMEOUT_SECONDS: u64 = 20;
const DEFAULT_LOG_DIR: &str = "logs/supabase_commands";
const TIMEOUT_STATUS_CODE: i32 = 124;

#[derive(Clone, Debug)]
struct SupabaseConfig {
    url: String,
    key: String,
    device_id: String,
}

#[derive(Debug)]
struct WorkerConfig {
    supabase: SupabaseConfig,
    upload_url: String,
    timeout: Duration,
    log_dir: PathBuf,
    repo_root: PathBuf,
}

#[derive(Debug)]
struct CommandRequest {
    command_id: String,
    device_id: String,
    command: String,
}

#[derive(Debug, Serialize)]
struct CommandResult {
    command: String,
    status_code: i32,
    stdout: String,
    stderr: String,
    timed_out: bool,
    started_at: String,
    finished_at: String,
}

#[derive(Debug)]
struct LogArchive {
    path: PathBuf,
    log_name: String,
}

#[derive(Debug, Deserialize)]
struct UploadResponse {
    data: Option<UploadData>,
}

#[derive(Debug, Deserialize)]
struct UploadData {
    file_url: Option<String>,
}

fn utc_now_iso() -> String {
    Utc::now().to_rfc3339()
}

fn load_supabase_config() -> Result<SupabaseConfig> {
    let url = env::var("SUPABASE_URL")
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| EMBEDDED_SUPABASE_URL.map(str::to_string))
        .context("missing SUPABASE_URL (runtime env or embedded compile-time value)")?;
    let key = env::var("SUPABASE_KEY")
        .or_else(|_| env::var("SUPABASE_ANON_KEY"))
        .or_else(|_| env::var("SUPABASE_PUBLISHABLE_KEY"))
        .ok()
        .filter(|v| !v.trim().is_empty())
        .or_else(|| EMBEDDED_SUPABASE_KEY.map(str::to_string))
        .context(
            "missing SUPABASE_KEY/SUPABASE_ANON_KEY (runtime env or embedded compile-time value)",
        )?;
    let device_id = env::var("DARTSNUT_SUPABASE_DEVICE_ID")
        .ok()
        .or_else(|| env::var("SUPABASE_DEVICE_ID").ok())
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| {
            resolve_device_id().unwrap_or_else(|e| {
                eprintln!("command-worker: failed to resolve BLE device_id: {e}");
                "UNKNOWN-DEVICE".to_string()
            })
        });
    Ok(SupabaseConfig {
        url,
        key,
        device_id,
    })
}

fn load_worker_config() -> Result<WorkerConfig> {
    let timeout_secs = env::var("DARTSNUT_COMMAND_TIMEOUT_SECONDS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(DEFAULT_TIMEOUT_SECONDS);
    let repo_root = env::current_dir().context("failed to resolve current directory")?;
    Ok(WorkerConfig {
        supabase: load_supabase_config()?,
        upload_url: env::var("DARTSNUT_LOG_UPLOAD_URL")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_UPLOAD_URL.to_string()),
        timeout: Duration::from_secs(timeout_secs),
        log_dir: env::var("DARTSNUT_COMMAND_LOG_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(DEFAULT_LOG_DIR)),
        repo_root,
    })
}

fn normalize_mac(value: &str) -> Option<String> {
    let trimmed = value.trim();
    let parts: Vec<&str> = trimmed.split(':').collect();
    if parts.len() != 6 {
        return None;
    }
    for p in &parts {
        if p.len() != 2 || !p.chars().all(|c| c.is_ascii_hexdigit()) {
            return None;
        }
    }
    Some(parts.join(":").to_ascii_uppercase())
}

fn parse_first_mac_from_output(output: &str) -> Option<String> {
    for token in output.split_whitespace() {
        let cleaned = token.trim_matches(|c: char| !c.is_ascii_hexdigit() && c != ':');
        if let Some(mac) = normalize_mac(cleaned) {
            return Some(mac);
        }
    }
    None
}

fn resolve_device_id() -> Result<String> {
    let bt_dir = "/sys/class/bluetooth";
    if let Ok(entries) = fs::read_dir(bt_dir) {
        let mut names: Vec<String> = entries
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|name| name.starts_with("hci"))
            .collect();
        names.sort();
        for name in names {
            let path = format!("{bt_dir}/{name}/address");
            if let Ok(raw) = fs::read_to_string(&path) {
                if let Some(mac) = normalize_mac(&raw) {
                    return Ok(mac);
                }
            }
        }
    }

    for cmd in [("hciconfig", vec!["-a"]), ("bluetoothctl", vec!["list"])] {
        if let Ok(out) = Command::new(cmd.0).args(cmd.1).output() {
            if out.status.success() {
                if let Ok(stdout) = String::from_utf8(out.stdout) {
                    if let Some(mac) = parse_first_mac_from_output(&stdout) {
                        return Ok(mac);
                    }
                }
            }
        }
    }

    Err(anyhow::anyhow!("no BLE adapter MAC found"))
}

fn command_source(device_id: &str) -> String {
    format!("dartsnut_command_bridge:{device_id}")
}

fn should_filter_command_echo(source: &str, device_id: &str) -> bool {
    source == command_source(device_id)
}

fn command_row_url(cfg: &SupabaseConfig) -> Result<Url> {
    let mut url = Url::parse(&format!(
        "{}/rest/v1/remote_device_commands",
        cfg.url.trim_end_matches('/')
    ))
    .context("invalid remote_device_commands url")?;
    url.query_pairs_mut()
        .append_pair("device_id", &format!("eq.{}", cfg.device_id));
    Ok(url)
}

fn command_completion_patch(device_id: &str, status_code: i32, log_filename: &str) -> Value {
    json!({
        "command": "",
        "status_code": status_code,
        "log_filename": log_filename,
        "last_update_source": command_source(device_id),
    })
}

fn update_command_completion(
    client: &Client,
    cfg: &SupabaseConfig,
    status_code: i32,
    log_filename: &str,
) -> Result<()> {
    client
        .patch(command_row_url(cfg)?)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=minimal")
        .json(&command_completion_patch(
            &cfg.device_id,
            status_code,
            log_filename,
        ))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn build_realtime_ws_url(cfg: &SupabaseConfig) -> Result<Url> {
    let mut base = Url::parse(&cfg.url).context("invalid SUPABASE_URL")?;
    let scheme = match base.scheme() {
        "https" => "wss",
        "http" => "ws",
        other => return Err(anyhow::anyhow!("unsupported supabase url scheme: {other}")),
    };
    base.set_scheme(scheme)
        .map_err(|_| anyhow::anyhow!("failed to set websocket scheme"))?;
    base.set_path("/realtime/v1/websocket");
    base.set_query(Some(&format!("apikey={}&vsn=1.0.0", cfg.key)));
    Ok(base)
}

fn set_ws_read_timeout(
    stream: &mut tungstenite::WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    timeout: Option<Duration>,
) {
    match stream.get_mut() {
        MaybeTlsStream::Plain(s) => {
            let _ = s.set_read_timeout(timeout);
        }
        MaybeTlsStream::Rustls(s) => {
            let _ = s.get_mut().set_read_timeout(timeout);
        }
        _ => {}
    }
}

fn extract_record_from_payload(payload: &Value) -> Option<&Value> {
    payload
        .get("record")
        .or_else(|| payload.get("new"))
        .or_else(|| payload.get("data").and_then(|d| d.get("record")))
        .or_else(|| payload.get("data").and_then(|d| d.get("new")))
}

fn build_command_request(record: &Value) -> Option<CommandRequest> {
    let command = record.get("command").and_then(|v| v.as_str())?.trim();
    if command.is_empty() {
        return None;
    }
    let device_id = record
        .get("device_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    let updated_at = record
        .get("updated_at")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let command_id = if !device_id.is_empty() && !updated_at.is_empty() {
        format!("{device_id}:{updated_at}")
    } else if !updated_at.is_empty() {
        updated_at.to_string()
    } else {
        command.to_string()
    };
    Some(CommandRequest {
        command_id,
        device_id,
        command: command.to_string(),
    })
}

fn prepare_shell_command(command: &str, euid: u32) -> String {
    let sudo_function = if euid == 0 {
        "sudo() { command \"$@\"; };"
    } else {
        "sudo() { command sudo -n \"$@\"; };"
    };
    format!("{sudo_function} {command}")
}

fn current_euid() -> u32 {
    unsafe { libc::geteuid() }
}

fn set_child_process_group() -> std::io::Result<()> {
    let rc = unsafe { libc::setpgid(0, 0) };
    if rc == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

fn kill_process_group(pid: u32) {
    unsafe {
        libc::killpg(pid as libc::pid_t, libc::SIGKILL);
    }
}

fn read_pipe_to_string<R: Read + Send + 'static>(mut reader: R) -> thread::JoinHandle<String> {
    thread::spawn(move || {
        let mut out = String::new();
        let _ = reader.read_to_string(&mut out);
        out
    })
}

fn run_command(command: &str, cwd: &Path, timeout: Duration) -> Result<CommandResult> {
    let started_at = utc_now_iso();
    let shell_command = prepare_shell_command(command, current_euid());
    let mut child = unsafe {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c")
            .arg(shell_command)
            .current_dir(cwd)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .pre_exec(set_child_process_group);
        cmd.spawn()
    }
    .with_context(|| format!("failed to run command: {command}"))?;

    let stdout_thread = child.stdout.take().map(read_pipe_to_string);
    let stderr_thread = child.stderr.take().map(read_pipe_to_string);
    let deadline = Instant::now() + timeout;
    let mut timed_out = false;
    let status_code;
    loop {
        if let Some(status) = child.try_wait()? {
            status_code = status.code().unwrap_or(1);
            break;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            kill_process_group(child.id());
            let _ = child.kill();
            let _ = child.wait();
            status_code = TIMEOUT_STATUS_CODE;
            break;
        }
        thread::sleep(Duration::from_millis(20));
    }

    let stdout = stdout_thread
        .map(|h| h.join().unwrap_or_default())
        .unwrap_or_default();
    let stderr = stderr_thread
        .map(|h| h.join().unwrap_or_default())
        .unwrap_or_default();

    Ok(CommandResult {
        command: command.to_string(),
        status_code,
        stdout,
        stderr,
        timed_out,
        started_at,
        finished_at: utc_now_iso(),
    })
}

fn safe_name(value: &str) -> String {
    let out: String = value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' || ch == '.' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = out.trim_matches('_');
    if trimmed.is_empty() {
        "command".to_string()
    } else {
        trimmed.to_string()
    }
}

fn write_log_archive(
    result: &CommandResult,
    log_dir: &Path,
    command_id: &str,
    device_id: &str,
) -> Result<LogArchive> {
    fs::create_dir_all(log_dir)?;
    let base = format!("{}-{}", safe_name(device_id), safe_name(command_id));
    let log_name = format!("{base}.log");
    let archive_path = log_dir.join(format!("{base}.tar.gz"));
    let log_path = log_dir.join(&log_name);
    let payload = json!({
        "device_id": device_id,
        "command_id": command_id,
        "command": result.command,
        "status_code": result.status_code,
        "timed_out": result.timed_out,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    });
    {
        let mut file = File::create(&log_path)?;
        writeln!(file, "{}", serde_json::to_string_pretty(&payload)?)?;
        writeln!(file, "\n--- stdout ---")?;
        write!(file, "{}", result.stdout)?;
        writeln!(file, "\n\n--- stderr ---")?;
        write!(file, "{}", result.stderr)?;
    }

    let tar_gz = File::create(&archive_path)?;
    let enc = GzEncoder::new(tar_gz, Compression::default());
    let mut tar = Builder::new(enc);
    tar.append_path_with_name(&log_path, &log_name)?;
    tar.finish()?;
    let _ = fs::remove_file(&log_path);

    Ok(LogArchive {
        path: archive_path,
        log_name,
    })
}

fn log_id_for_archive(path: &Path) -> String {
    let name = path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("command");
    name.strip_suffix(".tar.gz")
        .unwrap_or_else(|| {
            Path::new(name)
                .file_stem()
                .and_then(|v| v.to_str())
                .unwrap_or(name)
        })
        .to_string()
}

fn parse_upload_file_url(payload: &str) -> Result<String> {
    let parsed: UploadResponse = serde_json::from_str(payload)?;
    Ok(parsed
        .data
        .and_then(|d| d.file_url)
        .unwrap_or_default()
        .trim()
        .to_string())
}

fn upload_archive(
    client: &Client,
    archive_path: &Path,
    upload_url: &str,
    log_id: &str,
    device_id: &str,
    command_id: &str,
) -> Result<String> {
    let metadata = json!({
        "command_id": command_id,
        "reason": "engineer requested logs",
        "log_range": "latest",
    })
    .to_string();
    let file_name = archive_path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or("command.tar.gz")
        .to_string();
    let mut form = multipart::Form::new()
        .text("log_id", log_id.to_string())
        .text("device_id", device_id.to_string())
        .text("event", "engineer_command")
        .text("metadata", metadata)
        .part(
            "file",
            multipart::Part::file(archive_path)?
                .file_name(file_name)
                .mime_str("application/gzip")?,
        );
    if let Ok(version) = env::var("DARTSNUT_FIRMWARE_VERSION") {
        if !version.trim().is_empty() {
            form = form.text("device_version", version);
        }
    }
    if let Ok(version) = env::var("DARTSNUT_OS_VERSION") {
        if !version.trim().is_empty() {
            form = form.text("os_version", version);
        }
    }

    let text = client
        .post(upload_url)
        .multipart(form)
        .send()?
        .error_for_status()?
        .text()?;
    parse_upload_file_url(&text)
}

fn handle_command(client: &Client, cfg: &WorkerConfig, request: CommandRequest) -> (i32, String) {
    let result = match run_command(&request.command, &cfg.repo_root, cfg.timeout) {
        Ok(v) => v,
        Err(e) => CommandResult {
            command: request.command.clone(),
            status_code: 1,
            stdout: String::new(),
            stderr: e.to_string(),
            timed_out: false,
            started_at: utc_now_iso(),
            finished_at: utc_now_iso(),
        },
    };
    let status_code = result.status_code;
    let archive = match write_log_archive(
        &result,
        &cfg.log_dir,
        &request.command_id,
        &request.device_id,
    ) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("command-worker: failed to write log archive: {e}");
            return (status_code, String::new());
        }
    };
    let fallback = archive
        .path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or(&archive.log_name)
        .to_string();
    let log_id = log_id_for_archive(&archive.path);
    let log_reference = match upload_archive(
        client,
        &archive.path,
        &cfg.upload_url,
        &log_id,
        &request.device_id,
        &request.command_id,
    ) {
        Ok(file_url) if !file_url.is_empty() => file_url,
        Ok(_) => fallback,
        Err(e) => {
            eprintln!("command-worker: log upload failed: {e}");
            fallback
        }
    };
    (status_code, log_reference)
}

fn handle_realtime_record(client: &Client, worker_cfg: &WorkerConfig, record: &Value) {
    let source = record
        .get("last_update_source")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if should_filter_command_echo(source, &worker_cfg.supabase.device_id) {
        return;
    }
    let Some(request) = build_command_request(record) else {
        return;
    };
    let (status_code, log_filename) = handle_command(client, worker_cfg, request);
    if let Err(e) =
        update_command_completion(client, &worker_cfg.supabase, status_code, &log_filename)
    {
        eprintln!("command-worker: command completion update failed: {e}");
    }
}

fn run_realtime_loop(worker_cfg: WorkerConfig) -> Result<()> {
    let client = Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .context("failed to build http client")?;
    let ws_url = build_realtime_ws_url(&worker_cfg.supabase)?;
    let mut backoff_seconds = 1u64;
    loop {
        let (mut socket, _) = match connect(ws_url.as_str()) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("command-worker: realtime connect failed: {e}");
                thread::sleep(Duration::from_secs(backoff_seconds));
                backoff_seconds = (backoff_seconds * 2).min(30);
                continue;
            }
        };
        backoff_seconds = 1;
        set_ws_read_timeout(&mut socket, Some(Duration::from_secs(10)));
        let topic = "realtime:public:dartsnut_command_worker";
        let join_payload = json!({
            "topic": topic,
            "event": "phx_join",
            "payload": {
                "config": {
                    "broadcast": {"self": false},
                    "postgres_changes": [{
                        "event": "*",
                        "schema": "public",
                        "table": "remote_device_commands",
                        "filter": format!("device_id=eq.{}", worker_cfg.supabase.device_id),
                    }]
                },
                "access_token": worker_cfg.supabase.key,
            },
            "ref": "1",
        });
        if socket
            .send(Message::Text(join_payload.to_string().into()))
            .is_err()
        {
            thread::sleep(Duration::from_secs(backoff_seconds));
            backoff_seconds = (backoff_seconds * 2).min(30);
            continue;
        }

        let mut heartbeat_ref: u64 = 2;
        let mut ticks_since_heartbeat = 0u64;
        let mut heartbeats_unanswered = 0u64;
        loop {
            match socket.read() {
                Ok(Message::Text(text)) => {
                    heartbeats_unanswered = 0;
                    let parsed: Value = match serde_json::from_str(&text) {
                        Ok(v) => v,
                        Err(_) => continue,
                    };
                    if parsed.get("event").and_then(|e| e.as_str()) != Some("postgres_changes") {
                        continue;
                    }
                    let Some(payload) = parsed.get("payload") else {
                        continue;
                    };
                    if let Some(record) = extract_record_from_payload(payload) {
                        handle_realtime_record(&client, &worker_cfg, record);
                    }
                }
                Ok(_) => {}
                Err(e) => {
                    if let tungstenite::Error::Io(ioe) = &e {
                        if ioe.kind() == ErrorKind::WouldBlock || ioe.kind() == ErrorKind::TimedOut
                        {
                            ticks_since_heartbeat += 1;
                            if ticks_since_heartbeat >= 3 {
                                ticks_since_heartbeat = 0;
                                if heartbeats_unanswered >= 2 {
                                    eprintln!(
                                        "command-worker: realtime heartbeat unanswered; reconnecting"
                                    );
                                    break;
                                }
                                let hb_payload = json!({
                                    "topic": "phoenix",
                                    "event": "heartbeat",
                                    "payload": {},
                                    "ref": heartbeat_ref.to_string(),
                                });
                                heartbeat_ref += 1;
                                if socket
                                    .send(Message::Text(hb_payload.to_string().into()))
                                    .is_err()
                                {
                                    break;
                                }
                                heartbeats_unanswered += 1;
                            }
                            continue;
                        }
                    }
                    eprintln!("command-worker: realtime read error: {e}");
                    break;
                }
            }
        }
        thread::sleep(Duration::from_secs(backoff_seconds));
        backoff_seconds = (backoff_seconds * 2).min(30);
    }
}

fn main() -> Result<()> {
    run_realtime_loop(load_worker_config()?)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> SupabaseConfig {
        SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        }
    }

    #[test]
    fn command_table_url_targets_device_row() {
        let url = command_row_url(&cfg()).expect("url");
        assert_eq!(url.as_str(), "https://example.supabase.co/rest/v1/remote_device_commands?device_id=eq.AA%3ABB%3ACC%3ADD%3AEE%3AFF");
    }

    #[test]
    fn command_source_loopback_is_filtered() {
        assert!(should_filter_command_echo(
            "dartsnut_command_bridge:AA:BB:CC:DD:EE:FF",
            "AA:BB:CC:DD:EE:FF"
        ));
        assert!(!should_filter_command_echo(
            "mobile_app",
            "AA:BB:CC:DD:EE:FF"
        ));
    }

    #[test]
    fn command_completion_patch_shape_stores_file_url() {
        let patch = command_completion_patch(
            "AA:BB:CC:DD:EE:FF",
            0,
            "https://oss.example.com/device.log.gz",
        );
        assert_eq!(
            patch,
            json!({
                "command": "",
                "status_code": 0,
                "log_filename": "https://oss.example.com/device.log.gz",
                "last_update_source": "dartsnut_command_bridge:AA:BB:CC:DD:EE:FF"
            })
        );
    }

    #[test]
    fn command_request_requires_non_empty_command() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "  ls  ",
            "updated_at": "2026-07-02T00:00:00Z"
        });
        let request = build_command_request(&record).expect("request");
        assert_eq!(request.command, "ls");
        assert_eq!(request.command_id, "AA:BB:CC:DD:EE:FF:2026-07-02T00:00:00Z");
        assert!(build_command_request(&json!({"command": "   "})).is_none());
    }

    #[test]
    fn prepare_shell_command_strips_sudo_when_worker_is_root() {
        let prepared = prepare_shell_command("sudo systemctl restart dartsnut_python.service", 0);
        assert!(prepared.contains("sudo() { command \"$@\"; }"));
        assert!(prepared.contains("sudo systemctl restart dartsnut_python.service"));
    }

    #[test]
    fn prepare_shell_command_uses_noninteractive_sudo_when_not_root() {
        let prepared = prepare_shell_command("sudo systemctl status dartsnut_python.service", 1000);
        assert!(prepared.contains("sudo() { command sudo -n \"$@\"; }"));
        assert!(prepared.contains("sudo systemctl status dartsnut_python.service"));
    }

    #[test]
    fn successful_command_captures_stdout() {
        let tmp = env::temp_dir();
        let result = run_command("printf hello", &tmp, Duration::from_secs(20)).expect("run");
        assert_eq!(result.status_code, 0);
        assert_eq!(result.stdout, "hello");
        assert_eq!(result.stderr, "");
        assert!(!result.timed_out);
    }

    #[test]
    fn failed_command_captures_stderr_and_status() {
        let tmp = env::temp_dir();
        let result =
            run_command("printf nope >&2; exit 7", &tmp, Duration::from_secs(20)).expect("run");
        assert_eq!(result.status_code, 7);
        assert_eq!(result.stdout, "");
        assert_eq!(result.stderr, "nope");
        assert!(!result.timed_out);
    }

    #[test]
    fn timeout_kills_process_group_and_returns_124() {
        let tmp = env::temp_dir();
        let result = run_command("sleep 5", &tmp, Duration::from_millis(100)).expect("run");
        assert_eq!(result.status_code, TIMEOUT_STATUS_CODE);
        assert!(result.timed_out);
    }

    #[test]
    fn tarball_contains_command_log() {
        let unique = format!(
            "dartsnut-command-test-{}",
            Utc::now().timestamp_nanos_opt().unwrap_or_default()
        );
        let dir = env::temp_dir().join(unique);
        let result = CommandResult {
            command: "printf hello".to_string(),
            status_code: 0,
            stdout: "hello".to_string(),
            stderr: String::new(),
            timed_out: false,
            started_at: "2026-07-02T00:00:00Z".to_string(),
            finished_at: "2026-07-02T00:00:01Z".to_string(),
        };
        let archive =
            write_log_archive(&result, &dir, "cmd-1", "AA:BB:CC:DD:EE:FF").expect("archive");
        let file = File::open(&archive.path).expect("open archive");
        let dec = flate2::read::GzDecoder::new(file);
        let mut tar = tar::Archive::new(dec);
        let mut entries = tar.entries().expect("entries");
        let mut entry = entries.next().expect("one entry").expect("entry");
        assert_eq!(
            entry.path().expect("path").to_string_lossy(),
            archive.log_name
        );
        let mut content = String::new();
        entry.read_to_string(&mut content).expect("read");
        assert!(content.contains("printf hello"));
        assert!(content.contains("\"status_code\": 0"));
        assert!(content.contains("--- stdout ---"));
        assert!(content.contains("hello"));
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn upload_response_parses_file_url() {
        let file_url = parse_upload_file_url(
            r#"{"code":1001,"data":{"file_url":"https://oss.example.com/device.log.gz"}}"#,
        )
        .expect("parse");
        assert_eq!(file_url, "https://oss.example.com/device.log.gz");
    }

    #[test]
    fn log_id_strips_tar_gz_suffix() {
        assert_eq!(
            log_id_for_archive(Path::new("PD-123-cmd-1.tar.gz")),
            "PD-123-cmd-1"
        );
    }
}
