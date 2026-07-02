use anyhow::{Context, Result};
use chrono::Utc;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::ErrorKind;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message};
use url::Url;

const EMBEDDED_SUPABASE_URL: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_URL");
const EMBEDDED_SUPABASE_KEY: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_KEY");

#[derive(Debug, Deserialize)]
struct BridgeMessage {
    kind: String,
    payload: Value,
    #[serde(default)]
    source: Option<String>,
    #[serde(default)]
    r#ref: Option<String>,
    #[serde(default)]
    full: Option<bool>,
}

#[derive(Debug, Serialize)]
struct OutMessage<'a> {
    kind: &'a str,
    payload: Value,
}

#[derive(Clone)]
struct SupabaseConfig {
    url: String,
    key: String,
    device_id: String,
}

#[derive(Clone, Default)]
struct RestProbeSnapshot {
    latency_ms: Option<u64>,
    probe_ok: bool,
}

#[derive(Clone, Default)]
struct ProbeState {
    snapshot: RestProbeSnapshot,
    last_outbound_at: Option<Instant>,
}

const PROBE_INTERVAL_SECS: u64 = 60;

/// Max time a single write to the Python unix socket may block before the writer
/// thread gives up and exits for respawn. Bounds the head-of-line stall.
const WRITE_TIMEOUT_SECS: u64 = 15;

/// Bounded outbound queue depth. Snapshots are latest-wins on the Python side, so a
/// small buffer is enough; when full, `send_msg` drops rather than blocks the reader.
const OUT_CHANNEL_CAPACITY: usize = 256;

fn record_outbound_success(state: &mut ProbeState, latency_ms: u64) {
    state.snapshot = RestProbeSnapshot {
        latency_ms: Some(latency_ms),
        probe_ok: true,
    };
    state.last_outbound_at = Some(Instant::now());
}

fn record_outbound_failure(state: &mut ProbeState) {
    state.snapshot.probe_ok = false;
    state.snapshot.latency_ms = None;
}

fn outbound_within_idle_window(state: &ProbeState, idle: Duration) -> bool {
    state.last_outbound_at.is_some_and(|t| t.elapsed() < idle)
}

fn device_updated_at_iso_timestamp() -> String {
    Utc::now().to_rfc3339()
}

fn parse_socket_path() -> String {
    for arg in env::args() {
        if let Some(path) = arg.strip_prefix("--socket-path=") {
            return path.to_string();
        }
    }
    env::var("DARTSNUT_SUPABASE_SOCKET")
        .unwrap_or_else(|_| "/tmp/dartsnut-supabase-sync.sock".to_string())
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
                eprintln!("bridge: failed to resolve BLE device_id: {e}");
                "UNKNOWN-DEVICE".to_string()
            })
        });
    Ok(SupabaseConfig {
        url,
        key,
        device_id,
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

/// Outbound messages to the Python side are funneled through a bounded channel and
/// written by a single dedicated thread (see `main`). This decouples the realtime
/// reader from the blocking unix-socket write: if Python stalls and the pipe fills,
/// the reader keeps draining the websocket (so it still observes disconnects and
/// reconnects) instead of blocking inside `write_all` and wedging the connection.
type OutSender = mpsc::SyncSender<String>;
type CommandSender = mpsc::SyncSender<Value>;

fn send_msg(out: &OutSender, kind: &str, payload: Value) -> Result<()> {
    let msg = OutMessage { kind, payload };
    let line = serde_json::to_string(&msg)? + "\n";
    match out.try_send(line) {
        Ok(()) => Ok(()),
        // Channel full: the writer thread can't keep up because Python isn't draining.
        // Drop this message rather than block; the reader must stay responsive. The
        // writer thread's own write timeout is what ultimately forces a respawn.
        Err(mpsc::TrySendError::Full(_)) => Err(anyhow::anyhow!(
            "unix writer channel full; dropped {kind} message"
        )),
        Err(mpsc::TrySendError::Disconnected(_)) => {
            Err(anyhow::anyhow!("unix writer channel disconnected"))
        }
    }
}

const SOURCE_SUPABASE_BRIDGE: &str = "supabase_bridge";
const SOURCE_SUPABASE_BRIDGE_INIT: &str = "supabase_bridge_init";
const SOURCE_SUPABASE_BRIDGE_HEARTBEAT: &str = "supabase_bridge_heartbeat";
const COMMAND_CHANNEL_CAPACITY: usize = 16;

fn parse_command_socket_path() -> String {
    for arg in env::args() {
        if let Some(path) = arg.strip_prefix("--command-socket-path=") {
            return path.to_string();
        }
    }
    env::var("DARTSNUT_SUPABASE_COMMAND_SOCKET")
        .unwrap_or_else(|_| "/tmp/dartsnut-supabase-command.sock".to_string())
}

fn command_source(cfg: &SupabaseConfig) -> String {
    format!("dartsnut_command_bridge:{}", cfg.device_id)
}

fn should_filter_command_echo(source: &str, cfg: &SupabaseConfig) -> bool {
    source == command_source(cfg)
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

fn command_completion_patch(cfg: &SupabaseConfig, status_code: i64, log_filename: &str) -> Value {
    json!({
        "command": "",
        "status_code": status_code,
        "log_filename": log_filename,
        "last_update_source": command_source(cfg),
    })
}

fn update_command_completion(
    client: &Client,
    cfg: &SupabaseConfig,
    status_code: i64,
    log_filename: &str,
) -> Result<()> {
    client
        .patch(command_row_url(cfg)?)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=minimal")
        .json(&command_completion_patch(cfg, status_code, log_filename))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn rpc_apply_patch(
    client: &Client,
    cfg: &SupabaseConfig,
    patch: Value,
    full: bool,
    source_override: Option<&str>,
) -> Result<()> {
    let patch_obj = match patch {
        Value::Object(obj) => obj,
        other => {
            let mut obj = serde_json::Map::new();
            obj.insert("raw_payload".to_string(), other);
            obj
        }
    };
    // Games array merge is handled in Postgres (merge_games_array_by_id).
    let url = rpc_patch_url(cfg);
    let source = source_override
        .map(str::trim)
        .filter(|v| !v.is_empty())
        .unwrap_or(SOURCE_SUPABASE_BRIDGE);
    client
        .post(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=representation")
        .json(&json!({
            "p_device_id": cfg.device_id,
            "p_patch": Value::Object(patch_obj),
            "p_full": full,
            "p_source": source
        }))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn rpc_patch_url(cfg: &SupabaseConfig) -> String {
    format!(
        "{}/rest/v1/rpc/apply_remote_device_patch_v2",
        cfg.url.trim_end_matches('/')
    )
}

fn rpc_apply_patch_recorded(
    client: &Client,
    cfg: &SupabaseConfig,
    patch: Value,
    full: bool,
    source_override: Option<&str>,
    rpc_lock: &Arc<Mutex<()>>,
    probe_state: &Arc<Mutex<ProbeState>>,
) -> Result<()> {
    let _guard = rpc_lock
        .lock()
        .map_err(|_| anyhow::anyhow!("rpc apply lock poisoned"))?;
    let started = Instant::now();
    let result = rpc_apply_patch(client, cfg, patch, full, source_override);
    let elapsed_ms = started.elapsed().as_millis() as u64;
    if let Ok(mut state) = probe_state.lock() {
        match &result {
            Ok(()) => record_outbound_success(&mut state, elapsed_ms),
            Err(_) => record_outbound_failure(&mut state),
        }
    }
    result
}

fn remote_devices_query_url(cfg: &SupabaseConfig, select: &str) -> Result<Url> {
    let mut url = Url::parse(&format!(
        "{}/rest/v1/remote_devices",
        cfg.url.trim_end_matches('/')
    ))
    .context("invalid remote_devices url")?;
    url.query_pairs_mut()
        .append_pair("select", select)
        .append_pair("device_id", &format!("eq.{}", cfg.device_id))
        .append_pair("limit", "1");
    Ok(url)
}

fn probe_idle_device_updated_at_write(
    client: &Client,
    cfg: &SupabaseConfig,
    rpc_lock: &Arc<Mutex<()>>,
    probe_state: &Arc<Mutex<ProbeState>>,
) -> RestProbeSnapshot {
    let cached_latency_ms = probe_state.lock().ok().and_then(|s| s.snapshot.latency_ms);
    let mut patch = json!({
        "device_updated_at": device_updated_at_iso_timestamp(),
    });
    if let Some(ms) = cached_latency_ms {
        if let Some(obj) = patch.as_object_mut() {
            obj.insert("latency".to_string(), json!(ms));
        }
    }
    let _ = rpc_apply_patch_recorded(
        client,
        cfg,
        patch,
        false,
        Some(SOURCE_SUPABASE_BRIDGE_HEARTBEAT),
        rpc_lock,
        probe_state,
    );
    probe_state
        .lock()
        .map(|s| s.snapshot.clone())
        .unwrap_or_default()
}

fn run_probe_tick(
    client: &Client,
    cfg: &SupabaseConfig,
    rpc_lock: &Arc<Mutex<()>>,
    probe_state: &Arc<Mutex<ProbeState>>,
) -> RestProbeSnapshot {
    let idle = Duration::from_secs(PROBE_INTERVAL_SECS);
    if let Ok(state) = probe_state.lock() {
        if outbound_within_idle_window(&state, idle) {
            return state.snapshot.clone();
        }
    }
    probe_idle_device_updated_at_write(client, cfg, rpc_lock, probe_state)
}

fn send_bridge_health(writer: &OutSender, state: &str, probe: &RestProbeSnapshot) {
    let mut payload = json!({ "state": state });
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("rest_probe_ok".to_string(), json!(probe.probe_ok));
        if let Some(ms) = probe.latency_ms {
            obj.insert("rest_latency_ms".to_string(), json!(ms));
        }
    }
    let _ = send_msg(writer, "bridge_health", payload);
}

fn remote_device_exists(client: &Client, cfg: &SupabaseConfig) -> Result<bool> {
    let url = remote_devices_query_url(cfg, "device_id")?;
    let response = client
        .get(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .send()?;
    let rows: Vec<Value> = response.error_for_status()?.json()?;
    Ok(!rows.is_empty())
}

fn run_rest_probe_loop(
    writer: OutSender,
    cfg: SupabaseConfig,
    wake_rx: mpsc::Receiver<()>,
    realtime_connected: Arc<AtomicBool>,
    probe_state: Arc<Mutex<ProbeState>>,
    rpc_apply_lock: Arc<Mutex<()>>,
    probe_in_flight: Arc<AtomicBool>,
) {
    let client = match Client::builder().timeout(Duration::from_secs(3)).build() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("bridge: failed to build probe http client: {e}");
            return;
        }
    };

    loop {
        let _ = wake_rx.recv_timeout(Duration::from_secs(PROBE_INTERVAL_SECS));
        if probe_in_flight.swap(true, Ordering::AcqRel) {
            continue;
        }
        let snapshot = run_probe_tick(&client, &cfg, &rpc_apply_lock, &probe_state);
        let ws_state = if realtime_connected.load(Ordering::Relaxed) {
            "connected"
        } else {
            "disconnected"
        };
        send_bridge_health(&writer, ws_state, &snapshot);
        probe_in_flight.store(false, Ordering::Release);
    }
}

fn apply_initial_state_with_retry(
    client: &Client,
    cfg: &SupabaseConfig,
    patch: Value,
    rpc_lock: &Arc<Mutex<()>>,
    probe_state: &Arc<Mutex<ProbeState>>,
) -> Result<()> {
    let max_attempts = 8u32;
    let mut backoff_seconds = 1u64;
    let mut last_err: Option<anyhow::Error> = None;

    for attempt in 1..=max_attempts {
        match remote_device_exists(client, cfg) {
            Ok(false) => {
                match rpc_apply_patch_recorded(
                    client,
                    cfg,
                    patch.clone(),
                    true,
                    None,
                    rpc_lock,
                    probe_state,
                ) {
                    Ok(()) => return Ok(()),
                    Err(e) => {
                        eprintln!(
                            "bridge: initial full state write failed (attempt {attempt}/{max_attempts}): {e}"
                        );
                        last_err = Some(e);
                    }
                }
            }
            Ok(true) => {
                return Ok(());
            }
            Err(e) => {
                eprintln!(
                    "bridge: remote row lookup failed (attempt {attempt}/{max_attempts}): {e}"
                );
                match rpc_apply_patch_recorded(
                    client,
                    cfg,
                    patch.clone(),
                    true,
                    None,
                    rpc_lock,
                    probe_state,
                ) {
                    Ok(()) => return Ok(()),
                    Err(write_err) => {
                        eprintln!(
                            "bridge: fallback full init failed (attempt {attempt}/{max_attempts}): {write_err}"
                        );
                        last_err = Some(write_err);
                    }
                }
            }
        }

        if attempt < max_attempts {
            thread::sleep(Duration::from_secs(backoff_seconds));
            backoff_seconds = (backoff_seconds * 2).min(30);
        }
    }

    match last_err {
        Some(e) => Err(e),
        None => Err(anyhow::anyhow!(
            "failed to apply initial state for unknown reason after retries"
        )),
    }
}

fn build_realtime_ws_url(cfg: &SupabaseConfig) -> Result<Url> {
    let mut base = Url::parse(&cfg.url).context("invalid SUPABASE_URL")?;
    let scheme = match base.scheme() {
        "https" => "wss",
        "http" => "ws",
        other => {
            return Err(anyhow::anyhow!(
                "unsupported supabase url scheme: {}",
                other
            ))
        }
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

fn build_config_payload(record: &Value, state: &Value) -> Value {
    let mut payload = match state.as_object() {
        Some(obj) => Value::Object(obj.clone()),
        None => json!({}),
    };

    if let Some(obj) = payload.as_object_mut() {
        if let Some(updated_at) = record.get("updated_at").and_then(|v| v.as_str()) {
            obj.insert(
                "updated_at".to_string(),
                Value::String(updated_at.to_string()),
            );
        }
        if let Some(source) = record.get("last_update_source").and_then(|v| v.as_str()) {
            obj.insert(
                "last_update_source".to_string(),
                Value::String(source.to_string()),
            );
        }
    }

    payload
}

fn is_reset_confirmation_state(state: &Value) -> bool {
    let obj = match state.as_object() {
        Some(v) => v,
        None => return false,
    };
    if obj.get("ip_address").and_then(|v| v.as_str()) != Some("") {
        return false;
    }
    if let Some(ssid) = obj.get("ssid") {
        if ssid.as_str() != Some("") {
            return false;
        }
    }
    if let Some(pages) = obj.get("pages") {
        if pages.as_array().map_or(true, |a| !a.is_empty()) {
            return false;
        }
    }
    if let Some(games) = obj.get("games") {
        if games.as_array().map_or(true, |a| !a.is_empty()) {
            return false;
        }
    }
    let dim_window = match obj.get("dim_window").and_then(|v| v.as_object()) {
        Some(v) => v,
        None => return false,
    };
    !dim_window
        .get("dim_window_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn is_game_state_payload(state: &Value) -> bool {
    state.get("games").and_then(|v| v.as_array()).is_some()
}

fn should_filter_bridge_echo(source: &str, state: &Value) -> bool {
    if source == SOURCE_SUPABASE_BRIDGE_HEARTBEAT {
        return true;
    }
    if source == SOURCE_SUPABASE_BRIDGE_INIT {
        return false;
    }
    source == SOURCE_SUPABASE_BRIDGE
        && !is_reset_confirmation_state(state)
        && !is_game_state_payload(state)
}

fn build_command_payload(record: &Value) -> Option<Value> {
    let command = record.get("command").and_then(|v| v.as_str())?.trim();
    if command.is_empty() {
        return None;
    }
    let device_id = record
        .get("device_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
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
    Some(json!({
        "command_id": command_id,
        "device_id": device_id,
        "command": command,
    }))
}

fn send_command_msg(out: &CommandSender, payload: Value) {
    match out.try_send(payload) {
        Ok(()) => {}
        Err(mpsc::TrySendError::Full(_)) => {
            eprintln!("bridge: command channel full; dropped command")
        }
        Err(mpsc::TrySendError::Disconnected(_)) => {
            eprintln!("bridge: command channel disconnected; dropped command")
        }
    }
}

fn handle_realtime_record(
    record: &Value,
    cfg: &SupabaseConfig,
    writer: &OutSender,
    command_tx: &CommandSender,
    realtime_connected: &Arc<AtomicBool>,
    emit_health: &dyn Fn(&str),
) {
    let table = record.get("table").and_then(|v| v.as_str()).unwrap_or("");
    if table == "remote_device_commands" || record.get("command").is_some() {
        let source = record
            .get("last_update_source")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        if should_filter_command_echo(source, cfg) {
            return;
        }
        if let Some(command_payload) = build_command_payload(record) {
            send_command_msg(command_tx, command_payload);
        }
        return;
    }

    let state = match record.get("state") {
        Some(v) => v.clone(),
        None => return,
    };
    let source = record
        .get("last_update_source")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if should_filter_bridge_echo(source, &state) {
        return;
    }
    let config_payload = build_config_payload(record, &state);
    if send_msg(writer, "remote_row", config_payload).is_ok() {
        realtime_connected.store(true, Ordering::Relaxed);
        emit_health("connected");
    }
}

fn run_command_socket_loop(
    socket_path: String,
    command_rx: mpsc::Receiver<Value>,
    cfg: SupabaseConfig,
) {
    let client = match Client::builder().timeout(Duration::from_secs(10)).build() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("bridge: failed to build command http client: {e}");
            return;
        }
    };

    let mut pending: Option<Value> = None;
    loop {
        let mut stream = loop {
            match UnixStream::connect(&socket_path) {
                Ok(s) => break s,
                Err(_) => thread::sleep(Duration::from_millis(250)),
            }
        };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(120)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(WRITE_TIMEOUT_SECS)));
        let read_stream = match stream.try_clone() {
            Ok(s) => s,
            Err(e) => {
                eprintln!("bridge: command socket clone failed: {e}");
                thread::sleep(Duration::from_secs(1));
                continue;
            }
        };
        let mut reader = BufReader::new(read_stream);

        loop {
            let command_payload = match pending.take() {
                Some(v) => v,
                None => match command_rx.recv() {
                    Ok(v) => v,
                    Err(_) => return,
                },
            };
            let request = OutMessage {
                kind: "run_command",
                payload: command_payload.clone(),
            };
            let line = match serde_json::to_string(&request) {
                Ok(v) => v + "\n",
                Err(e) => {
                    eprintln!("bridge: command serialize failed: {e}");
                    continue;
                }
            };
            if stream.write_all(line.as_bytes()).is_err() || stream.flush().is_err() {
                eprintln!("bridge: command socket write failed; reconnecting");
                pending = Some(command_payload);
                break;
            }

            let mut response_line = String::new();
            match reader.read_line(&mut response_line) {
                Ok(0) => {
                    eprintln!("bridge: command socket closed; reconnecting");
                    pending = Some(command_payload);
                    break;
                }
                Ok(_) => {}
                Err(e) => {
                    eprintln!("bridge: command socket read failed: {e}; reconnecting");
                    pending = Some(command_payload);
                    break;
                }
            }
            let parsed: Value = match serde_json::from_str(response_line.trim()) {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("bridge: command response parse failed: {e}");
                    continue;
                }
            };
            if parsed.get("kind").and_then(|v| v.as_str()) != Some("command_result") {
                continue;
            }
            let payload = match parsed.get("payload") {
                Some(v) => v,
                None => continue,
            };
            let status_code = payload
                .get("status_code")
                .and_then(|v| v.as_i64())
                .unwrap_or(1);
            let log_filename = payload
                .get("log_filename")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            if let Err(e) = update_command_completion(&client, &cfg, status_code, log_filename) {
                eprintln!("bridge: command completion update failed: {e}");
            }
        }
    }
}

fn run_realtime_loop(
    writer: OutSender,
    command_tx: CommandSender,
    cfg: SupabaseConfig,
    probe_wake_tx: mpsc::Sender<()>,
    realtime_connected: Arc<AtomicBool>,
    probe_state: Arc<Mutex<ProbeState>>,
) {
    let emit_health = |state: &str| {
        let snap = probe_state
            .lock()
            .expect("probe state lock poisoned")
            .snapshot
            .clone();
        send_bridge_health(&writer, state, &snap);
    };
    let signal_probe = || {
        let _ = probe_wake_tx.send(());
    };
    let ws_url = match build_realtime_ws_url(&cfg) {
        Ok(u) => u,
        Err(e) => {
            eprintln!("bridge: invalid realtime url: {e}");
            realtime_connected.store(false, Ordering::Relaxed);
            emit_health("disconnected");
            signal_probe();
            return;
        }
    };

    let mut backoff_seconds = 1u64;
    loop {
        let connect_result = connect(ws_url.as_str());
        let (mut socket, _) = match connect_result {
            Ok(v) => v,
            Err(e) => {
                eprintln!("bridge: realtime connect failed: {e}");
                realtime_connected.store(false, Ordering::Relaxed);
                emit_health("disconnected");
                signal_probe();
                thread::sleep(Duration::from_secs(backoff_seconds));
                backoff_seconds = (backoff_seconds * 2).min(30);
                continue;
            }
        };
        backoff_seconds = 1;
        set_ws_read_timeout(&mut socket, Some(Duration::from_secs(10)));

        let topic = "realtime:public:dartsnut_bridge";
        let join_payload = json!({
            "topic": topic,
            "event": "phx_join",
            "payload": {
                "config": {
                    "broadcast": {"self": false},
                    "postgres_changes": [
                        {
                            "event": "*",
                            "schema": "public",
                            "table": "remote_devices",
                            "filter": format!("device_id=eq.{}", cfg.device_id),
                        },
                        {
                            "event": "*",
                            "schema": "public",
                            "table": "remote_device_commands",
                            "filter": format!("device_id=eq.{}", cfg.device_id),
                        }
                    ]
                },
                "access_token": cfg.key,
            },
            "ref": "1",
        });
        if socket
            .send(Message::Text(join_payload.to_string().into()))
            .is_err()
        {
            realtime_connected.store(false, Ordering::Relaxed);
            emit_health("disconnected");
            signal_probe();
            thread::sleep(Duration::from_secs(backoff_seconds));
            backoff_seconds = (backoff_seconds * 2).min(30);
            continue;
        }

        realtime_connected.store(true, Ordering::Relaxed);
        emit_health("connected");
        signal_probe();
        let mut heartbeat_ref: u64 = 2;
        let mut ticks_since_heartbeat = 0u64;
        // Heartbeats sent since the last inbound message. Any frame from the server
        // (postgres_changes, phx_reply, etc.) proves the connection is alive and
        // resets this. If two consecutive heartbeats go unanswered (~60s of total
        // silence) the socket is half-open: force a reconnect instead of trusting it.
        let mut heartbeats_unanswered = 0u64;

        loop {
            match socket.read() {
                Ok(msg) => {
                    if let Message::Text(text) = msg {
                        // Any inbound text frame is proof of liveness.
                        heartbeats_unanswered = 0;
                        let parsed: Value = match serde_json::from_str(&text) {
                            Ok(v) => v,
                            Err(_) => continue,
                        };
                        let event = parsed.get("event").and_then(|e| e.as_str()).unwrap_or("");
                        if event != "postgres_changes" {
                            continue;
                        }
                        let payload = match parsed.get("payload") {
                            Some(p) => p,
                            None => continue,
                        };
                        let record = match extract_record_from_payload(payload) {
                            Some(r) => r,
                            None => continue,
                        };
                        handle_realtime_record(
                            record,
                            &cfg,
                            &writer,
                            &command_tx,
                            &realtime_connected,
                            &emit_health,
                        );
                    }
                }
                Err(e) => {
                    if let tungstenite::Error::Io(ioe) = &e {
                        if ioe.kind() == ErrorKind::WouldBlock || ioe.kind() == ErrorKind::TimedOut
                        {
                            ticks_since_heartbeat += 1;
                            if ticks_since_heartbeat >= 3 {
                                ticks_since_heartbeat = 0;
                                // A prior heartbeat is still unanswered after a full
                                // interval: the connection is half-open. Drop it.
                                if heartbeats_unanswered >= 2 {
                                    eprintln!(
                                        "bridge: realtime heartbeat unanswered; reconnecting"
                                    );
                                    realtime_connected.store(false, Ordering::Relaxed);
                                    emit_health("disconnected");
                                    signal_probe();
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
                                    realtime_connected.store(false, Ordering::Relaxed);
                                    emit_health("disconnected");
                                    signal_probe();
                                    break;
                                }
                                heartbeats_unanswered += 1;
                            }
                            continue;
                        }
                    }
                    eprintln!("bridge: realtime read error: {e}");
                    realtime_connected.store(false, Ordering::Relaxed);
                    emit_health("disconnected");
                    signal_probe();
                    break;
                }
            }
        }
        thread::sleep(Duration::from_secs(backoff_seconds));
        backoff_seconds = (backoff_seconds * 2).min(30);
    }
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().collect();
    if args.iter().any(|a| a == "--probe-latency") {
        let cfg = load_supabase_config()?;
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .context("failed to build probe http client")?;
        let rpc_apply_lock = Arc::new(Mutex::new(()));
        let probe_state = Arc::new(Mutex::new(ProbeState::default()));
        let snapshot =
            probe_idle_device_updated_at_write(&client, &cfg, &rpc_apply_lock, &probe_state);
        if snapshot.probe_ok {
            println!(
                "{}",
                json!({
                    "rest_latency_ms": snapshot.latency_ms,
                    "rest_probe_ok": true
                })
            );
            return Ok(());
        }
        println!("{}", json!({ "rest_probe_ok": false }));
        std::process::exit(1);
    }

    let socket_path = parse_socket_path();
    let command_socket_path = parse_command_socket_path();
    let cfg = load_supabase_config()?;
    let client = Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .context("failed to build http client")?;

    let stream = loop {
        match UnixStream::connect(&socket_path) {
            Ok(s) => break s,
            Err(_) => thread::sleep(Duration::from_millis(250)),
        }
    };
    stream
        .set_read_timeout(Some(Duration::from_secs(1)))
        .context("failed to set read timeout")?;
    // Bound how long a single write to Python may block. Without this a stalled
    // consumer would let `write_all` block forever, freezing whichever thread holds
    // the writer and (previously) wedging the realtime reader so it never reconnected.
    stream
        .set_write_timeout(Some(Duration::from_secs(WRITE_TIMEOUT_SECS)))
        .context("failed to set write timeout")?;
    let write_stream = stream.try_clone()?;
    let reader = BufReader::new(stream);

    // All outbound messages funnel through this bounded channel and are written by a
    // single dedicated thread. Producers (`send_msg`) never block on the socket: if
    // the channel fills (Python not draining) they drop the message and keep going.
    let (out_tx, out_rx) = mpsc::sync_channel::<String>(OUT_CHANNEL_CAPACITY);
    thread::spawn(move || {
        let mut write_stream = write_stream;
        for line in out_rx {
            if write_stream.write_all(line.as_bytes()).is_err() || write_stream.flush().is_err() {
                // Python side is gone or unresponsive past the write timeout. The
                // bridge can't recover this unix socket on its own (Python owns the
                // listener); exit so the supervisor respawns us with a fresh pipe.
                eprintln!("bridge: unix writer failed; exiting for respawn");
                std::process::exit(1);
            }
        }
    });
    let writer = out_tx;
    let (command_tx, command_rx) = mpsc::sync_channel::<Value>(COMMAND_CHANNEL_CAPACITY);

    send_msg(&writer, "ready", json!({}))?;

    let probe_state = Arc::new(Mutex::new(ProbeState::default()));
    let rpc_apply_lock = Arc::new(Mutex::new(()));
    let realtime_connected = Arc::new(AtomicBool::new(false));
    let probe_in_flight = Arc::new(AtomicBool::new(false));
    let (probe_wake_tx, probe_wake_rx) = mpsc::channel();

    let writer_probe = writer.clone();
    let cfg_probe = cfg.clone();
    let probe_state_probe = Arc::clone(&probe_state);
    let rpc_apply_lock_probe = Arc::clone(&rpc_apply_lock);
    let realtime_connected_probe = Arc::clone(&realtime_connected);
    let probe_in_flight_probe = Arc::clone(&probe_in_flight);
    thread::spawn(move || {
        run_rest_probe_loop(
            writer_probe,
            cfg_probe,
            probe_wake_rx,
            realtime_connected_probe,
            probe_state_probe,
            rpc_apply_lock_probe,
            probe_in_flight_probe,
        );
    });
    let _ = probe_wake_tx.send(());

    let cfg_command = cfg.clone();
    thread::spawn(move || {
        run_command_socket_loop(command_socket_path, command_rx, cfg_command);
    });

    let writer_clone = writer.clone();
    let command_tx_rt = command_tx.clone();
    let cfg_clone = cfg.clone();
    let probe_state_rt = Arc::clone(&probe_state);
    let probe_wake_tx_rt = probe_wake_tx.clone();
    let realtime_connected_rt = Arc::clone(&realtime_connected);
    thread::spawn(move || {
        run_realtime_loop(
            writer_clone,
            command_tx_rt,
            cfg_clone,
            probe_wake_tx_rt,
            realtime_connected_rt,
            probe_state_rt,
        );
    });

    let rpc_apply_lock_main = Arc::clone(&rpc_apply_lock);
    let probe_state_main = Arc::clone(&probe_state);
    for line in reader.lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        if line.trim().is_empty() {
            continue;
        }
        let msg: BridgeMessage = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let write_ref = msg.r#ref.clone().unwrap_or_else(|| "legacy".to_string());
        let is_full = msg.full.unwrap_or(false);
        match msg.kind.as_str() {
            "initial_state" | "rpc_patch" => {
                let full = is_full || msg.kind == "initial_state";
                let result = if full && msg.kind == "initial_state" {
                    apply_initial_state_with_retry(
                        &client,
                        &cfg,
                        msg.payload,
                        &rpc_apply_lock_main,
                        &probe_state_main,
                    )
                } else {
                    rpc_apply_patch_recorded(
                        &client,
                        &cfg,
                        msg.payload,
                        full,
                        msg.source.as_deref(),
                        &rpc_apply_lock_main,
                        &probe_state_main,
                    )
                };
                match result {
                    Ok(()) => {
                        let _ = send_msg(&writer, "ack", json!({"ref": write_ref}));
                    }
                    Err(e) => {
                        let _ = send_msg(
                            &writer,
                            "error",
                            json!({
                                "ref": write_ref,
                                "message": e.to_string(),
                            }),
                        );
                    }
                }
            }
            "device_state" => {
                match rpc_apply_patch_recorded(
                    &client,
                    &cfg,
                    msg.payload,
                    false,
                    msg.source.as_deref(),
                    &rpc_apply_lock_main,
                    &probe_state_main,
                ) {
                    Ok(()) => {
                        let _ = send_msg(&writer, "ack", json!({"ref": write_ref}));
                    }
                    Err(e) => {
                        let _ = send_msg(
                            &writer,
                            "error",
                            json!({
                                "ref": write_ref,
                                "message": e.to_string(),
                            }),
                        );
                    }
                }
            }
            _ => {}
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn reset_confirmation_shape_is_detected() {
        let state = json!({
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": false}
        });
        assert!(is_reset_confirmation_state(&state));
    }

    #[test]
    fn reset_confirmation_requires_empty_ip_and_dim_disabled() {
        let non_reset_state = json!({
            "ip_address": "192.168.1.2",
            "dim_window": {"dim_window_enabled": false}
        });
        assert!(!is_reset_confirmation_state(&non_reset_state));

        let non_reset_state2 = json!({
            "ip_address": "",
            "dim_window": {"dim_window_enabled": true}
        });
        assert!(!is_reset_confirmation_state(&non_reset_state2));
    }

    #[test]
    fn game_state_payload_detects_games_array() {
        assert!(is_game_state_payload(&json!({"games": []})));
        assert!(is_game_state_payload(
            &json!({"games": [{"id":"g1","status":"ready"}]})
        ));
        assert!(!is_game_state_payload(&json!({"games": null})));
        assert!(!is_game_state_payload(&json!({"brightness": 70})));
    }

    #[test]
    fn bridge_echo_filter_allows_init_source_reset_roundtrip() {
        let state = json!({
            "ip_address": "",
            "ssid": "",
            "pages": [],
            "games": [],
            "dim_window": {"dim_window_enabled": false}
        });
        assert!(!should_filter_bridge_echo(
            SOURCE_SUPABASE_BRIDGE_INIT,
            &state
        ));
    }

    #[test]
    fn bridge_echo_filter_drops_heartbeat_even_with_full_state() {
        let state = json!({
            "device_updated_at": "2026-06-05T08:40:58Z",
            "pages": [{"uuid": "p1"}],
            "games": [{"id": "chess", "status": "ready"}]
        });
        assert!(should_filter_bridge_echo(
            SOURCE_SUPABASE_BRIDGE_HEARTBEAT,
            &state
        ));
    }

    #[test]
    fn bridge_echo_filter_still_forwards_bridge_games_echo() {
        let state = json!({
            "games": [{"id": "chess", "status": "ready"}]
        });
        assert!(!should_filter_bridge_echo(SOURCE_SUPABASE_BRIDGE, &state));
    }

    #[test]
    fn build_config_payload_includes_record_metadata() {
        let record = json!({
            "updated_at": "2026-04-01T12:00:01Z",
            "last_update_source": "mobile_app_test"
        });
        let state = json!({
            "games": [{"id": "chess", "status": "ready"}]
        });

        let payload = build_config_payload(&record, &state);
        assert_eq!(
            payload.get("updated_at").and_then(|v| v.as_str()),
            Some("2026-04-01T12:00:01Z")
        );
        assert_eq!(
            payload.get("last_update_source").and_then(|v| v.as_str()),
            Some("mobile_app_test")
        );
        assert!(payload.get("games").is_some());
    }

    #[test]
    fn rpc_patch_url_uses_v2_function() {
        let cfg = SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };

        assert_eq!(
            rpc_patch_url(&cfg),
            "https://example.supabase.co/rest/v1/rpc/apply_remote_device_patch_v2"
        );
    }

    #[test]
    fn command_table_url_targets_device_row() {
        let cfg = SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };

        let url = command_row_url(&cfg).expect("url");

        assert_eq!(url.as_str(), "https://example.supabase.co/rest/v1/remote_device_commands?device_id=eq.AA%3ABB%3ACC%3ADD%3AEE%3AFF");
    }

    #[test]
    fn command_source_loopback_is_filtered() {
        let cfg = SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };

        assert!(should_filter_command_echo(&command_source(&cfg), &cfg));
        assert!(!should_filter_command_echo("mobile_app", &cfg));
    }

    #[test]
    fn command_payload_requires_non_empty_command() {
        let record = json!({
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "  ls  ",
            "updated_at": "2026-07-02T00:00:00Z"
        });

        let payload = build_command_payload(&record).expect("payload");

        assert_eq!(payload.get("command").and_then(|v| v.as_str()), Some("ls"));
        assert_eq!(
            payload.get("command_id").and_then(|v| v.as_str()),
            Some("AA:BB:CC:DD:EE:FF:2026-07-02T00:00:00Z")
        );
        assert!(build_command_payload(&json!({"command": "   "})).is_none());
    }

    #[test]
    fn command_completion_patch_shape_clears_command() {
        let cfg = SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };

        let patch = command_completion_patch(&cfg, 124, "cmd.tar.gz");

        assert_eq!(
            patch,
            json!({
                "command": "",
                "status_code": 124,
                "log_filename": "cmd.tar.gz",
                "last_update_source": "dartsnut_command_bridge:AA:BB:CC:DD:EE:FF"
            })
        );
    }

    #[test]
    fn command_record_routes_to_command_channel_only() {
        let cfg = SupabaseConfig {
            url: "https://example.supabase.co".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let (config_tx, config_rx) = mpsc::sync_channel::<String>(1);
        let (command_tx, command_rx) = mpsc::sync_channel::<Value>(1);
        let connected = Arc::new(AtomicBool::new(false));
        let record = json!({
            "table": "remote_device_commands",
            "device_id": "AA:BB:CC:DD:EE:FF",
            "command": "ls",
            "updated_at": "2026-07-02T00:00:00Z",
            "last_update_source": "mobile_app"
        });

        handle_realtime_record(&record, &cfg, &config_tx, &command_tx, &connected, &|_| {});

        assert!(config_rx.try_recv().is_err());
        let payload = command_rx.try_recv().expect("command payload");
        assert_eq!(payload.get("command").and_then(|v| v.as_str()), Some("ls"));
    }

    #[test]
    fn rpc_games_patch_posts_to_v2_merge_rpc_as_partial() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let (tx, rx) = mpsc::channel();
        let server = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 8192];
                let n = stream.read(&mut buf).unwrap_or(0);
                let request = String::from_utf8_lossy(&buf[..n]).to_string();
                tx.send(request).expect("send request");
                let body = b"[]";
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.write_all(body);
            }
        });

        let cfg = SupabaseConfig {
            url: format!("http://127.0.0.1:{}", addr.port()),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .expect("client");

        rpc_apply_patch(
            &client,
            &cfg,
            json!({"games": [{"id": "pico8", "status": "ready", "version": "1"}]}),
            false,
            None,
        )
        .expect("rpc patch");
        let request = rx.recv_timeout(Duration::from_secs(3)).expect("request");
        let _ = server.join();

        let first_line = request.lines().next().unwrap_or("");
        assert_eq!(
            first_line,
            "POST /rest/v1/rpc/apply_remote_device_patch_v2 HTTP/1.1"
        );
        let body = request.split("\r\n\r\n").nth(1).expect("http body");
        let body: Value = serde_json::from_str(body).expect("json body");
        assert_eq!(body.get("p_full"), Some(&json!(false)));
        assert_eq!(
            body.get("p_patch").and_then(|p| p.get("games")),
            Some(&json!([{"id": "pico8", "status": "ready", "version": "1"}]))
        );
    }

    #[test]
    fn initial_state_existing_device_only_checks_row_existence() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        listener
            .set_nonblocking(true)
            .expect("set listener nonblocking");
        let (tx, rx) = mpsc::channel();
        let server = thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_millis(300);
            while Instant::now() < deadline {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let mut buf = [0u8; 8192];
                        let n = stream.read(&mut buf).unwrap_or(0);
                        let request = String::from_utf8_lossy(&buf[..n]).to_string();
                        tx.send(request).expect("send request");
                        let body = br#"[{"device_id":"AA:BB:CC:DD:EE:FF"}]"#;
                        let response = format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        );
                        let _ = stream.write_all(response.as_bytes());
                        let _ = stream.write_all(body);
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => break,
                }
            }
        });

        let cfg = SupabaseConfig {
            url: format!("http://127.0.0.1:{}", addr.port()),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .expect("client");
        let rpc_lock = Arc::new(Mutex::new(()));
        let probe_state = Arc::new(Mutex::new(ProbeState::default()));

        apply_initial_state_with_retry(
            &client,
            &cfg,
            json!({"time_zone": "", "firmware": {"version": "1.0.0", "update": false}}),
            &rpc_lock,
            &probe_state,
        )
        .expect("initial state should be skipped for existing row");

        let request = rx.recv_timeout(Duration::from_secs(3)).expect("request");
        let _ = server.join();
        assert!(request.starts_with("GET /rest/v1/remote_devices?"));
        assert!(rx.try_recv().is_err());
    }

    #[test]
    fn outbound_within_idle_window_true_after_recent_outbound() {
        let mut state = ProbeState::default();
        record_outbound_success(&mut state, 42);
        assert!(outbound_within_idle_window(
            &state,
            Duration::from_secs(PROBE_INTERVAL_SECS)
        ));
    }

    #[test]
    fn outbound_within_idle_window_false_when_never_outbound() {
        let state = ProbeState::default();
        assert!(!outbound_within_idle_window(
            &state,
            Duration::from_secs(PROBE_INTERVAL_SECS)
        ));
    }

    #[test]
    fn record_outbound_failure_clears_probe_ok_but_keeps_last_outbound_at() {
        let mut state = ProbeState::default();
        record_outbound_success(&mut state, 10);
        let at = state.last_outbound_at;
        record_outbound_failure(&mut state);
        assert!(!state.snapshot.probe_ok);
        assert!(state.snapshot.latency_ms.is_none());
        assert_eq!(state.last_outbound_at, at);
    }

    #[test]
    fn run_probe_tick_reuses_snapshot_when_outbound_recent() {
        let cfg = SupabaseConfig {
            url: "http://127.0.0.1:1".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let client = Client::builder()
            .timeout(Duration::from_millis(50))
            .build()
            .expect("client");
        let probe_state = Arc::new(Mutex::new(ProbeState::default()));
        {
            let mut s = probe_state.lock().expect("lock");
            record_outbound_success(&mut s, 99);
        }
        let rpc_lock = Arc::new(Mutex::new(()));
        let snap = run_probe_tick(&client, &cfg, &rpc_lock, &probe_state);
        assert!(snap.probe_ok);
        assert_eq!(snap.latency_ms, Some(99));
    }

    #[test]
    fn probe_idle_device_updated_at_write_records_latency_on_rpc_success() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let (tx, rx) = mpsc::channel();
        let server = thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buf = [0u8; 4096];
                let n = stream.read(&mut buf).unwrap_or(0);
                let request = String::from_utf8_lossy(&buf[..n]).to_string();
                tx.send(request).expect("send request");
                let body = b"[]";
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
                let _ = stream.write_all(body);
            }
        });

        let cfg = SupabaseConfig {
            url: format!("http://127.0.0.1:{}", addr.port()),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let client = Client::builder()
            .timeout(Duration::from_secs(3))
            .build()
            .expect("client");
        let rpc_lock = Arc::new(Mutex::new(()));
        let probe_state = Arc::new(Mutex::new(ProbeState::default()));
        {
            let mut s = probe_state.lock().expect("lock");
            record_outbound_success(&mut s, 77);
        }
        let snapshot = probe_idle_device_updated_at_write(&client, &cfg, &rpc_lock, &probe_state);
        let request = rx.recv_timeout(Duration::from_secs(3)).expect("request");
        let _ = server.join();

        assert!(snapshot.probe_ok);
        assert!(snapshot.latency_ms.is_some());
        let body = request.split("\r\n\r\n").nth(1).expect("http body");
        let body: Value = serde_json::from_str(body).expect("json body");
        let patch = body.get("p_patch").expect("p_patch");
        assert_eq!(patch.get("latency"), Some(&json!(77)));
        assert!(patch.get("device_updated_at").is_some());
        let at = probe_state
            .lock()
            .expect("lock")
            .last_outbound_at
            .expect("outbound time");
        assert!(outbound_within_idle_window(
            &ProbeState {
                snapshot: snapshot.clone(),
                last_outbound_at: Some(at),
            },
            Duration::from_secs(PROBE_INTERVAL_SECS)
        ));
    }

    #[test]
    fn probe_idle_device_updated_at_write_fails_on_unreachable_host() {
        let cfg = SupabaseConfig {
            url: "http://127.0.0.1:1".to_string(),
            key: "test-key".to_string(),
            device_id: "AA:BB:CC:DD:EE:FF".to_string(),
        };
        let client = Client::builder()
            .timeout(Duration::from_millis(200))
            .build()
            .expect("client");
        let rpc_lock = Arc::new(Mutex::new(()));
        let probe_state = Arc::new(Mutex::new(ProbeState::default()));
        let snapshot = probe_idle_device_updated_at_write(&client, &cfg, &rpc_lock, &probe_state);
        assert!(!snapshot.probe_ok);
        assert!(snapshot.latency_ms.is_none());
    }
}
