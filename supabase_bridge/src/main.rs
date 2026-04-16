use anyhow::{Context, Result};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::ErrorKind;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message};
use url::Url;

const EMBEDDED_SUPABASE_URL: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_URL");
const EMBEDDED_SUPABASE_KEY: Option<&str> = option_env!("DARTSNUT_EMBEDDED_SUPABASE_KEY");

#[derive(Debug, Deserialize)]
struct BridgeMessage {
    kind: String,
    payload: Value,
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
    Ok(SupabaseConfig { url, key, device_id })
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

    for cmd in [
        ("hciconfig", vec!["-a"]),
        ("bluetoothctl", vec!["list"]),
    ] {
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

fn send_msg(writer: &Arc<Mutex<UnixStream>>, kind: &str, payload: Value) -> Result<()> {
    let msg = OutMessage { kind, payload };
    let line = serde_json::to_string(&msg)? + "\n";
    let mut lock = writer.lock().expect("socket writer lock poisoned");
    lock.write_all(line.as_bytes())?;
    lock.flush()?;
    Ok(())
}

fn rpc_apply_patch(client: &Client, cfg: &SupabaseConfig, patch: Value, full: bool) -> Result<()> {
    let mut patch_obj = match patch {
        Value::Object(obj) => obj,
        other => {
            let mut obj = serde_json::Map::new();
            obj.insert("raw_payload".to_string(), other);
            obj
        }
    };
    if !full {
        merge_games_patch_with_remote_state(client, cfg, &mut patch_obj);
    }
    let url = format!("{}/rest/v1/rpc/apply_remote_device_patch", cfg.url.trim_end_matches('/'));
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
            "p_source": "supabase_bridge"
        }))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn merge_games_patch_with_remote_state(
    client: &Client,
    cfg: &SupabaseConfig,
    patch_obj: &mut serde_json::Map<String, Value>,
) {
    let incoming_games = match patch_obj.get("games").and_then(|v| v.as_array()) {
        Some(v) if !v.is_empty() => v.clone(),
        _ => return,
    };

    let mut url = match Url::parse(&format!(
        "{}/rest/v1/remote_devices",
        cfg.url.trim_end_matches('/')
    )) {
        Ok(v) => v,
        Err(_) => return,
    };
    url.query_pairs_mut()
        .append_pair("select", "state")
        .append_pair("device_id", &format!("eq.{}", cfg.device_id))
        .append_pair("limit", "1");

    let rows: Vec<Value> = match client
        .get(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .send()
    {
        Ok(resp) => match resp.error_for_status() {
            Ok(ok) => match ok.json() {
                Ok(parsed) => parsed,
                Err(_) => return,
            },
            Err(_) => return,
        },
        Err(_) => return,
    };
    let existing_games = rows
        .first()
        .and_then(|row| row.get("state"))
        .and_then(|state| state.get("games"))
        .and_then(|games| games.as_array());
    let Some(existing_games) = existing_games else {
        return;
    };

    let mut merged_games = existing_games.clone();
    for incoming in &incoming_games {
        let incoming_id = match incoming.get("id").and_then(|v| v.as_str()) {
            Some(v) if !v.trim().is_empty() => v.to_string(),
            _ => continue,
        };
        let mut replaced = false;
        for existing in &mut merged_games {
            if existing.get("id").and_then(|v| v.as_str()) == Some(incoming_id.as_str()) {
                *existing = incoming.clone();
                replaced = true;
                break;
            }
        }
        if !replaced {
            merged_games.push(incoming.clone());
        }
    }
    patch_obj.insert("games".to_string(), Value::Array(merged_games));
}

fn remote_device_exists(client: &Client, cfg: &SupabaseConfig) -> Result<bool> {
    let mut url = Url::parse(&format!(
        "{}/rest/v1/remote_devices",
        cfg.url.trim_end_matches('/')
    ))
    .context("invalid remote_devices url")?;
    url.query_pairs_mut()
        .append_pair("select", "device_id")
        .append_pair("device_id", &format!("eq.{}", cfg.device_id))
        .append_pair("limit", "1");

    let rows: Vec<Value> = client
        .get(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .send()?
        .error_for_status()?
        .json()?;
    Ok(!rows.is_empty())
}

fn strip_games_for_existing_device_initial_state(mut patch: Value) -> Value {
    if let Some(obj) = patch.as_object_mut() {
        obj.remove("games");
    }
    patch
}

fn build_realtime_ws_url(cfg: &SupabaseConfig) -> Result<Url> {
    let mut base = Url::parse(&cfg.url).context("invalid SUPABASE_URL")?;
    let scheme = match base.scheme() {
        "https" => "wss",
        "http" => "ws",
        other => return Err(anyhow::anyhow!("unsupported supabase url scheme: {}", other)),
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
            obj.insert("updated_at".to_string(), Value::String(updated_at.to_string()));
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
    state
        .get("games")
        .and_then(|v| v.as_array())
        .is_some()
}

fn run_realtime_loop(writer: Arc<Mutex<UnixStream>>, cfg: SupabaseConfig) {
    let ws_url = match build_realtime_ws_url(&cfg) {
        Ok(u) => u,
        Err(e) => {
            eprintln!("bridge: invalid realtime url: {e}");
            let _ = send_msg(&writer, "bridge_health", json!({ "state": "disconnected" }));
            return;
        }
    };

    let mut backoff_seconds = 1u64;
    let mut sent_initial = false;
    loop {
        let connect_result = connect(ws_url.as_str());
        let (mut socket, _) = match connect_result {
            Ok(v) => v,
            Err(e) => {
                eprintln!("bridge: realtime connect failed: {e}");
                let _ = send_msg(&writer, "bridge_health", json!({ "state": "disconnected" }));
                thread::sleep(Duration::from_secs(backoff_seconds));
                backoff_seconds = (backoff_seconds * 2).min(30);
                continue;
            }
        };
        backoff_seconds = 1;
        set_ws_read_timeout(&mut socket, Some(Duration::from_secs(10)));

        let topic = "realtime:public:remote_devices";
        let join_payload = json!({
            "topic": topic,
            "event": "phx_join",
            "payload": {
                "config": {
                    "broadcast": {"self": false},
                    "postgres_changes": [{
                        "event": "*",
                        "schema": "public",
                        "table": "remote_devices",
                        "filter": format!("device_id=eq.{}", cfg.device_id),
                    }]
                },
                "access_token": cfg.key,
            },
            "ref": "1",
        });
        if socket
            .send(Message::Text(join_payload.to_string().into()))
            .is_err()
        {
            let _ = send_msg(&writer, "bridge_health", json!({ "state": "disconnected" }));
            thread::sleep(Duration::from_secs(backoff_seconds));
            backoff_seconds = (backoff_seconds * 2).min(30);
            continue;
        }

        let _ = send_msg(&writer, "bridge_health", json!({ "state": "connected" }));
        let mut heartbeat_ref: u64 = 2;
        let mut ticks_since_heartbeat = 0u64;

        loop {
            match socket.read() {
                Ok(msg) => {
                    if let Message::Text(text) = msg {
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
                        let state = match record.get("state") {
                            Some(v) => v.clone(),
                            None => continue,
                        };
                        let source = record
                            .get("last_update_source")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        if source == "supabase_bridge"
                            && !is_reset_confirmation_state(&state)
                            && !is_game_state_payload(&state)
                        {
                            continue;
                        }
                        let kind = if sent_initial { "config" } else { "config_initial" };
                        let config_payload = build_config_payload(record, &state);
                        if send_msg(&writer, kind, config_payload).is_ok() {
                            sent_initial = true;
                            let _ = send_msg(&writer, "bridge_health", json!({ "state": "connected" }));
                        }
                    }
                }
                Err(e) => {
                    if let tungstenite::Error::Io(ioe) = &e {
                        if ioe.kind() == ErrorKind::WouldBlock || ioe.kind() == ErrorKind::TimedOut
                        {
                            ticks_since_heartbeat += 1;
                            if ticks_since_heartbeat >= 3 {
                                ticks_since_heartbeat = 0;
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
                                    let _ = send_msg(
                                        &writer,
                                        "bridge_health",
                                        json!({ "state": "disconnected" }),
                                    );
                                    break;
                                }
                            }
                            continue;
                        }
                    }
                    eprintln!("bridge: realtime read error: {e}");
                    let _ = send_msg(&writer, "bridge_health", json!({ "state": "disconnected" }));
                    break;
                }
            }
        }
        thread::sleep(Duration::from_secs(backoff_seconds));
        backoff_seconds = (backoff_seconds * 2).min(30);
    }
}

fn main() -> Result<()> {
    let socket_path = parse_socket_path();
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
    let writer = Arc::new(Mutex::new(stream.try_clone()?));
    let reader = BufReader::new(stream);

    send_msg(&writer, "ready", json!({}))?;

    let writer_clone = Arc::clone(&writer);
    let cfg_clone = cfg.clone();
    thread::spawn(move || run_realtime_loop(writer_clone, cfg_clone));

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
        match msg.kind.as_str() {
            "initial_state" => {
                let mut patch = msg.payload;
                match remote_device_exists(&client, &cfg) {
                    Ok(false) => {
                        let _ = rpc_apply_patch(&client, &cfg, patch, true);
                    }
                    Ok(true) => {
                        patch = strip_games_for_existing_device_initial_state(patch);
                        let _ = rpc_apply_patch(&client, &cfg, patch, false);
                    }
                    Err(e) => {
                        eprintln!("bridge: remote row lookup failed, falling back to full init: {e}");
                        let _ = rpc_apply_patch(&client, &cfg, patch, true);
                    }
                }
            }
            "device_state" => {
                let _ = rpc_apply_patch(&client, &cfg, msg.payload, false);
            }
            _ => {}
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

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
        assert!(is_game_state_payload(&json!({"games": [{"id":"g1","status":"ready"}]})));
        assert!(!is_game_state_payload(&json!({"games": null})));
        assert!(!is_game_state_payload(&json!({"brightness": 70})));
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
    fn strip_games_for_existing_device_initial_state_removes_games_field() {
        let patch = json!({
            "games": [{"id": "chess", "status": "ready"}],
            "volume": 50
        });
        let out = strip_games_for_existing_device_initial_state(patch);
        assert!(out.get("games").is_none());
        assert_eq!(out.get("volume").and_then(|v| v.as_i64()), Some(50));
    }

    #[test]
    fn merge_games_patch_replaces_matching_game_and_preserves_others() {
        let mut patch_obj = serde_json::Map::new();
        patch_obj.insert(
            "games".to_string(),
            json!([{"id": "chess", "status": "downloading", "version": "2.0.0"}]),
        );
        let existing_games = json!([
            {"id": "chess", "status": "ready", "version": "1.0.0"},
            {"id": "pong", "status": "ready", "version": "1.1.0"}
        ])
        .as_array()
        .cloned()
        .unwrap_or_default();

        let mut merged_games = existing_games.clone();
        for incoming in patch_obj
            .get("games")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let incoming_id = incoming
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let mut replaced = false;
            for existing in &mut merged_games {
                if existing.get("id").and_then(|v| v.as_str()) == Some(incoming_id.as_str()) {
                    *existing = incoming.clone();
                    replaced = true;
                    break;
                }
            }
            if !replaced {
                merged_games.push(incoming.clone());
            }
        }
        patch_obj.insert("games".to_string(), Value::Array(merged_games));

        let games = patch_obj.get("games").and_then(|v| v.as_array()).cloned();
        assert_eq!(
            games,
            Some(vec![
                json!({"id": "chess", "status": "downloading", "version": "2.0.0"}),
                json!({"id": "pong", "status": "ready", "version": "1.1.0"})
            ])
        );
    }
}
