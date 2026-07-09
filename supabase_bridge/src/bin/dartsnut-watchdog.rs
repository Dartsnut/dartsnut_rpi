use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
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
use std::sync::mpsc;
use std::thread;
use std::time::Duration;
use tar::Builder;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message};
use url::Url;

const EMBEDDED_SUPABASE_URL: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_URL");
const EMBEDDED_SUPABASE_KEY: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_KEY");
const DEFAULT_UPLOAD_URL: &str = "https://api.dartsnut.com/v1/mobile/device-log/upload";
const DEFAULT_LOG_DIR: &str = "logs/supabase_watchdog";
const STOPPED_STATUS_CODE: i32 = 130;

#[derive(Clone, Debug)]
struct SupabaseConfig {
    url: String,
    key: String,
    device_id: String,
}

#[derive(Clone, Debug)]
struct WorkerConfig {
    supabase: SupabaseConfig,
    upload_url: String,
    log_dir: PathBuf,
    repo_root: PathBuf,
}

#[derive(Clone, Debug)]
struct WatchdogTask {
    command_id: String,
    device_id: String,
    command: String,
    command_token: String,
}

#[derive(Debug, Serialize)]
struct WatchdogResult {
    command: String,
    status_code: i32,
    stdout: String,
    stderr: String,
    stopped: bool,
    started_at: String,
    finished_at: String,
}

#[derive(Clone, Debug)]
enum WorkerMessage {
    Record(Value),
}

#[derive(Debug)]
enum TaskInterrupt {
    Stop,
}

#[derive(Debug)]
struct RunningTask {
    task: WatchdogTask,
    started_at: String,
    stop_tx: mpsc::Sender<TaskInterrupt>,
    result_rx: mpsc::Receiver<WatchdogResult>,
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
                eprintln!("watchdog: failed to resolve BLE device_id: {e}");
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
    let repo_root = env::current_dir().context("failed to resolve current directory")?;
    Ok(WorkerConfig {
        supabase: load_supabase_config()?,
        upload_url: env::var("DARTSNUT_LOG_UPLOAD_URL")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_UPLOAD_URL.to_string()),
        log_dir: env::var("DARTSNUT_WATCHDOG_LOG_DIR")
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

fn watchdog_source(device_id: &str) -> String {
    format!("dartsnut_watchdog:{device_id}")
}

fn should_filter_watchdog_echo(source: &str, device_id: &str) -> bool {
    source == watchdog_source(device_id)
}

fn watchdog_row_url(cfg: &SupabaseConfig) -> Result<Url> {
    let mut url = Url::parse(&format!(
        "{}/rest/v1/remote_device_commands",
        cfg.url.trim_end_matches('/')
    ))
    .context("invalid remote_device_commands url")?;
    url.query_pairs_mut()
        .append_pair("device_id", &format!("eq.{}", cfg.device_id));
    Ok(url)
}

fn watchdog_claim_patch(device_id: &str, command_token: &str, started_at: &str) -> Value {
    json!({
        "command_token": command_token,
        "running_command_token": command_token,
        "started_at": started_at,
        "stop_requested_at": Value::Null,
        "last_update_source": watchdog_source(device_id),
    })
}

fn watchdog_completion_patch(
    device_id: &str,
    _command_token: &str,
    status_code: i32,
    log_filename: &str,
) -> Value {
    json!({
        "command": "",
        "running_command_token": "",
        "stop_requested_at": Value::Null,
        "status_code": status_code,
        "log_filename": log_filename,
        "last_update_source": watchdog_source(device_id),
    })
}

fn update_watchdog_claim(
    client: &Client,
    cfg: &SupabaseConfig,
    task: &WatchdogTask,
    started_at: &str,
) -> Result<()> {
    client
        .patch(watchdog_row_url(cfg)?)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=minimal")
        .json(&watchdog_claim_patch(
            &cfg.device_id,
            &task.command_token,
            started_at,
        ))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn update_watchdog_completion_guarded(
    client: &Client,
    cfg: &SupabaseConfig,
    task: &WatchdogTask,
    status_code: i32,
    log_filename: &str,
) -> Result<()> {
    let Some(record) = fetch_current_watchdog_record(client, cfg)? else {
        return Ok(());
    };
    let running_token = record
        .get("running_command_token")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let current_command_token = record
        .get("command_token")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let current_command = record
        .get("command")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if running_token != task.command_token
        || current_command_token != task.command_token
        || current_command != task.command
    {
        return Ok(());
    }
    client
        .patch(watchdog_row_url(cfg)?)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=minimal")
        .json(&watchdog_completion_patch(
            &cfg.device_id,
            &task.command_token,
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

fn record_text<'a>(record: &'a Value, key: &str) -> &'a str {
    record
        .get(key)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
}

fn build_watchdog_task(record: &Value) -> Option<WatchdogTask> {
    let command = record_text(record, "command");
    if command.is_empty() {
        return None;
    }
    let device_id = record_text(record, "device_id").to_string();
    let explicit_token = record_text(record, "command_token");
    let updated_at = record_text(record, "updated_at");
    let command_token = if !explicit_token.is_empty() {
        explicit_token
    } else if !updated_at.is_empty() {
        updated_at
    } else {
        command
    };
    if record_text(record, "running_command_token") == command_token {
        return None;
    }
    let command_id = if !device_id.is_empty() {
        format!("{device_id}:{command_token}")
    } else {
        command_token.to_string()
    };
    Some(WatchdogTask {
        command_id,
        device_id,
        command: command.to_string(),
        command_token: command_token.to_string(),
    })
}

fn build_claimed_watchdog_task(record: &Value) -> Option<WatchdogTask> {
    let command = record_text(record, "command");
    if command.is_empty() {
        return None;
    }
    let running_token = record_text(record, "running_command_token");
    if running_token.is_empty() {
        return None;
    }
    let device_id = record_text(record, "device_id").to_string();
    let command_id = if !device_id.is_empty() {
        format!("{device_id}:{running_token}")
    } else {
        running_token.to_string()
    };
    Some(WatchdogTask {
        command_id,
        device_id,
        command: command.to_string(),
        command_token: running_token.to_string(),
    })
}

fn record_requests_stop(record: &Value, active_started_at: &str) -> bool {
    let stop_requested_at = record_text(record, "stop_requested_at");
    if stop_requested_at.is_empty() || active_started_at.trim().is_empty() {
        return false;
    }
    let Ok(stop_at) = DateTime::parse_from_rfc3339(stop_requested_at) else {
        return false;
    };
    let Ok(started_at) = DateTime::parse_from_rfc3339(active_started_at) else {
        return false;
    };
    stop_at > started_at
}

fn prepare_shell_task(command: &str, euid: u32) -> String {
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

fn execute_task_until_stopped(
    command: &str,
    cwd: &Path,
    stop_rx: &mpsc::Receiver<TaskInterrupt>,
) -> Result<WatchdogResult> {
    let started_at = utc_now_iso();
    let shell_command = prepare_shell_task(command, current_euid());
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
    let mut stopped = false;
    let status_code;
    loop {
        if let Some(status) = child.try_wait()? {
            status_code = status.code().unwrap_or(1);
            break;
        }
        match stop_rx.try_recv() {
            Ok(TaskInterrupt::Stop) | Err(mpsc::TryRecvError::Disconnected) => {
                stopped = true;
                kill_process_group(child.id());
                let _ = child.kill();
                let _ = child.wait();
                status_code = STOPPED_STATUS_CODE;
                break;
            }
            Err(mpsc::TryRecvError::Empty) => {}
        }
        thread::sleep(Duration::from_millis(20));
    }

    let stdout = stdout_thread
        .map(|h| h.join().unwrap_or_default())
        .unwrap_or_default();
    let stderr = stderr_thread
        .map(|h| h.join().unwrap_or_default())
        .unwrap_or_default();

    Ok(WatchdogResult {
        command: command.to_string(),
        status_code,
        stdout,
        stderr,
        stopped,
        started_at,
        finished_at: utc_now_iso(),
    })
}

fn spawn_task_executor(
    task: WatchdogTask,
    cwd: PathBuf,
) -> (mpsc::Sender<TaskInterrupt>, mpsc::Receiver<WatchdogResult>) {
    let (stop_tx, stop_rx) = mpsc::channel();
    let (result_tx, result_rx) = mpsc::channel();
    thread::spawn(move || {
        let result = match execute_task_until_stopped(&task.command, &cwd, &stop_rx) {
            Ok(v) => v,
            Err(e) => WatchdogResult {
                command: task.command.clone(),
                status_code: 1,
                stdout: String::new(),
                stderr: e.to_string(),
                stopped: false,
                started_at: utc_now_iso(),
                finished_at: utc_now_iso(),
            },
        };
        let _ = result_tx.send(result);
    });
    (stop_tx, result_rx)
}

fn fetch_current_watchdog_record(client: &Client, cfg: &SupabaseConfig) -> Result<Option<Value>> {
    let mut url = watchdog_row_url(cfg)?;
    url.query_pairs_mut().append_pair(
        "select",
        "device_id,command,command_token,running_command_token,started_at,stop_requested_at,updated_at,last_update_source",
    );
    let rows: Vec<Value> = client
        .get(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .send()?
        .error_for_status()?
        .json()?;
    Ok(rows.into_iter().next())
}

fn start_task(client: &Client, cfg: &WorkerConfig, task: WatchdogTask) -> Option<RunningTask> {
    let started_at = utc_now_iso();
    if let Err(e) = update_watchdog_claim(client, &cfg.supabase, &task, &started_at) {
        eprintln!("watchdog: task claim failed: {e}");
        return None;
    }
    let (stop_tx, result_rx) = spawn_task_executor(task.clone(), cfg.repo_root.clone());
    Some(RunningTask {
        task,
        started_at,
        stop_tx,
        result_rx,
    })
}

fn upload_result_log(
    client: &Client,
    cfg: &WorkerConfig,
    task: &WatchdogTask,
    result: &WatchdogResult,
) -> String {
    let archive = match write_log_archive(result, &cfg.log_dir, &task.command_id, &task.device_id) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("watchdog: failed to write log archive: {e}");
            return String::new();
        }
    };
    let fallback = archive
        .path
        .file_name()
        .and_then(|v| v.to_str())
        .unwrap_or(&archive.log_name)
        .to_string();
    let log_id = log_id_for_archive(&archive.path);
    match upload_archive(
        client,
        &archive.path,
        &cfg.upload_url,
        &log_id,
        &task.device_id,
        &task.command_id,
    ) {
        Ok(file_url) if !file_url.is_empty() => file_url,
        Ok(_) => fallback,
        Err(e) => {
            eprintln!("watchdog: log upload failed: {e}");
            fallback
        }
    }
}

fn finish_task(client: &Client, cfg: &WorkerConfig, running: RunningTask, result: WatchdogResult) {
    let status_code = result.status_code;
    let log_filename = upload_result_log(client, cfg, &running.task, &result);
    if let Err(e) = update_watchdog_completion_guarded(
        client,
        &cfg.supabase,
        &running.task,
        status_code,
        &log_filename,
    ) {
        eprintln!("watchdog: task completion update failed: {e}");
    }
}

fn clear_abandoned_claim(client: &Client, cfg: &WorkerConfig, task: WatchdogTask) {
    let now = utc_now_iso();
    let result = WatchdogResult {
        command: task.command.clone(),
        status_code: STOPPED_STATUS_CODE,
        stdout: String::new(),
        stderr: "watchdog restarted while command was already claimed; command not rerun"
            .to_string(),
        stopped: true,
        started_at: now.clone(),
        finished_at: now,
    };
    let running = RunningTask {
        task,
        started_at: result.started_at.clone(),
        stop_tx: mpsc::channel().0,
        result_rx: mpsc::channel().1,
    };
    finish_task(client, cfg, running, result);
}

fn process_worker_message(
    client: &Client,
    cfg: &WorkerConfig,
    message: WorkerMessage,
    running: &mut Option<RunningTask>,
    pending: &mut Option<WatchdogTask>,
) {
    let WorkerMessage::Record(record) = message;
    let source = record_text(&record, "last_update_source");
    if running.is_none() && pending.is_none() {
        if let Some(task) = build_claimed_watchdog_task(&record) {
            clear_abandoned_claim(client, cfg, task);
            return;
        }
    }
    if should_filter_watchdog_echo(source, &cfg.supabase.device_id) {
        return;
    }
    if let Some(current) = running.as_ref() {
        if record_requests_stop(&record, &current.started_at) {
            let _ = current.stop_tx.send(TaskInterrupt::Stop);
            return;
        }
    }
    let Some(task) = build_watchdog_task(&record) else {
        return;
    };
    if let Some(current) = running.as_ref() {
        if current.task.command_token != task.command_token {
            let _ = current.stop_tx.send(TaskInterrupt::Stop);
            *pending = Some(task);
        }
        return;
    }
    *running = start_task(client, cfg, task);
}

fn run_task_worker(rx: mpsc::Receiver<WorkerMessage>, client: Client, cfg: WorkerConfig) {
    let mut running: Option<RunningTask> = None;
    let mut pending: Option<WatchdogTask> = None;
    loop {
        if let Some(current) = running.take() {
            match current.result_rx.try_recv() {
                Ok(result) => {
                    finish_task(&client, &cfg, current, result);
                    if let Some(task) = pending.take() {
                        running = start_task(&client, &cfg, task);
                    }
                }
                Err(mpsc::TryRecvError::Empty) => {
                    running = Some(current);
                }
                Err(mpsc::TryRecvError::Disconnected) => {}
            }
        }
        match rx.recv_timeout(Duration::from_millis(20)) {
            Ok(message) => {
                process_worker_message(&client, &cfg, message, &mut running, &mut pending);
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
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
    result: &WatchdogResult,
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
        "stopped": result.stopped,
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

fn run_realtime_loop(worker_cfg: WorkerConfig) -> Result<()> {
    let client = Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .context("failed to build http client")?;
    let ws_url = build_realtime_ws_url(&worker_cfg.supabase)?;
    let (worker_tx, worker_rx) = mpsc::channel();
    let worker_client = client.clone();
    let worker_cfg = worker_cfg;
    let worker_loop_cfg = worker_cfg.clone();
    thread::spawn(move || run_task_worker(worker_rx, worker_client, worker_loop_cfg));
    let mut backoff_seconds = 1u64;
    loop {
        let (mut socket, _) = match connect(ws_url.as_str()) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("watchdog: realtime connect failed: {e}");
                thread::sleep(Duration::from_secs(backoff_seconds));
                backoff_seconds = (backoff_seconds * 2).min(30);
                continue;
            }
        };
        backoff_seconds = 1;
        match fetch_current_watchdog_record(&client, &worker_cfg.supabase) {
            Ok(Some(record)) => {
                let _ = worker_tx.send(WorkerMessage::Record(record));
            }
            Ok(None) => {}
            Err(e) => eprintln!("watchdog: current row fetch failed: {e}"),
        }
        set_ws_read_timeout(&mut socket, Some(Duration::from_secs(10)));
        let topic = "realtime:public:dartsnut_watchdog";
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
                        let _ = worker_tx.send(WorkerMessage::Record(record.clone()));
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
                                        "watchdog: realtime heartbeat unanswered; reconnecting"
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
                    eprintln!("watchdog: realtime read error: {e}");
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
    fn watchdog_table_url_targets_device_row() {
        let url = watchdog_row_url(&cfg()).expect("url");
        assert_eq!(url.as_str(), "https://example.supabase.co/rest/v1/remote_device_commands?device_id=eq.AA%3ABB%3ACC%3ADD%3AEE%3AFF");
    }

    #[test]
    fn watchdog_source_loopback_is_filtered() {
        assert!(should_filter_watchdog_echo(
            "dartsnut_watchdog:AA:BB:CC:DD:EE:FF",
            "AA:BB:CC:DD:EE:FF"
        ));
        assert!(!should_filter_watchdog_echo(
            "mobile_app",
            "AA:BB:CC:DD:EE:FF"
        ));
    }

    #[test]
    fn watchdog_completion_patch_shape_stores_file_url() {
        let patch = watchdog_completion_patch(
            "AA:BB:CC:DD:EE:FF",
            "cmd-token-1",
            0,
            "https://oss.example.com/device.log.gz",
        );
        assert_eq!(
            patch,
            json!({
                "command": "",
                "running_command_token": "",
                "stop_requested_at": Value::Null,
                "status_code": 0,
                "log_filename": "https://oss.example.com/device.log.gz",
                "last_update_source": "dartsnut_watchdog:AA:BB:CC:DD:EE:FF"
            })
        );
    }

    #[test]
    fn watchdog_claim_patch_marks_running_token() {
        let patch =
            watchdog_claim_patch("AA:BB:CC:DD:EE:FF", "cmd-token-1", "2026-07-08T01:02:03Z");
        assert_eq!(
            patch,
            json!({
                "running_command_token": "cmd-token-1",
                "command_token": "cmd-token-1",
                "started_at": "2026-07-08T01:02:03Z",
                "stop_requested_at": Value::Null,
                "last_update_source": "dartsnut_watchdog:AA:BB:CC:DD:EE:FF"
            })
        );
    }

    #[test]
    fn watchdog_task_requires_non_empty_payload_and_uses_token() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "  ls  ",
            "command_token": "cmd-token-1",
            "updated_at": "2026-07-02T00:00:00Z"
        });
        let request = build_watchdog_task(&record).expect("request");
        assert_eq!(request.command, "ls");
        assert_eq!(request.command_token, "cmd-token-1");
        assert_eq!(request.command_id, "AA:BB:CC:DD:EE:FF:cmd-token-1");
        assert!(build_watchdog_task(&json!({"command": "   "})).is_none());
    }

    #[test]
    fn watchdog_task_uses_updated_at_fallback_token_for_legacy_rows() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "printf legacy",
            "command_token": "",
            "updated_at": "2026-07-02T00:00:00Z"
        });
        let request = build_watchdog_task(&record).expect("request");
        assert_eq!(request.command_token, "2026-07-02T00:00:00Z");
    }

    #[test]
    fn watchdog_task_skips_already_claimed_rows() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "printf old",
            "command_token": "cmd-token-1",
            "running_command_token": "cmd-token-1",
            "updated_at": "2026-07-02T00:00:00Z"
        });
        assert!(build_watchdog_task(&record).is_none());
    }

    #[test]
    fn claimed_watchdog_task_can_be_cleared_without_rerun() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "printf old",
            "command_token": "cmd-token-1",
            "running_command_token": "cmd-token-1",
            "updated_at": "2026-07-02T00:00:00Z"
        });
        let request = build_claimed_watchdog_task(&record).expect("request");
        assert_eq!(request.command, "printf old");
        assert_eq!(request.command_token, "cmd-token-1");
        assert_eq!(request.command_id, "AA:BB:CC:DD:EE:FF:cmd-token-1");
    }

    #[test]
    fn stop_request_applies_only_after_started_at() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "stop_requested_at": "2026-07-08T01:02:04Z"
        });
        assert!(record_requests_stop(&record, "2026-07-08T01:02:03Z"));
        assert!(!record_requests_stop(&record, "2026-07-08T01:02:05Z"));
    }

    #[test]
    fn stoppable_task_kills_process_group_and_returns_130() {
        let tmp = env::temp_dir();
        let (tx, rx) = std::sync::mpsc::channel();
        let handle =
            thread::spawn(move || execute_task_until_stopped("sleep 5", &tmp, &rx).expect("run"));
        thread::sleep(Duration::from_millis(100));
        tx.send(TaskInterrupt::Stop).expect("stop");
        let result = handle.join().expect("join");
        assert_eq!(result.status_code, STOPPED_STATUS_CODE);
        assert!(result.stopped);
    }

    #[test]
    fn prepare_shell_task_strips_sudo_when_root() {
        let prepared = prepare_shell_task("sudo systemctl restart dartsnut_python.service", 0);
        assert!(prepared.contains("sudo() { command \"$@\"; }"));
        assert!(prepared.contains("sudo systemctl restart dartsnut_python.service"));
    }

    #[test]
    fn prepare_shell_task_uses_noninteractive_sudo_when_not_root() {
        let prepared = prepare_shell_task("sudo systemctl status dartsnut_python.service", 1000);
        assert!(prepared.contains("sudo() { command sudo -n \"$@\"; }"));
        assert!(prepared.contains("sudo systemctl status dartsnut_python.service"));
    }

    #[test]
    fn successful_task_captures_stdout() {
        let tmp = env::temp_dir();
        let (_tx, rx) = std::sync::mpsc::channel();
        let result = execute_task_until_stopped("printf hello", &tmp, &rx).expect("run");
        assert_eq!(result.status_code, 0);
        assert_eq!(result.stdout, "hello");
        assert_eq!(result.stderr, "");
        assert!(!result.stopped);
    }

    #[test]
    fn failed_task_captures_stderr_and_status() {
        let tmp = env::temp_dir();
        let (_tx, rx) = std::sync::mpsc::channel();
        let result = execute_task_until_stopped("printf nope >&2; exit 7", &tmp, &rx).expect("run");
        assert_eq!(result.status_code, 7);
        assert_eq!(result.stdout, "");
        assert_eq!(result.stderr, "nope");
        assert!(!result.stopped);
    }

    #[test]
    fn tarball_contains_watchdog_log() {
        let unique = format!(
            "dartsnut-watchdog-test-{}",
            Utc::now().timestamp_nanos_opt().unwrap_or_default()
        );
        let dir = env::temp_dir().join(unique);
        let result = WatchdogResult {
            command: "printf hello".to_string(),
            status_code: 0,
            stdout: "hello".to_string(),
            stderr: String::new(),
            stopped: false,
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
