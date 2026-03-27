use anyhow::{Context, Result};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

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

#[derive(Debug, Deserialize)]
struct DeviceRow {
    state: Value,
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
    let url = env::var("SUPABASE_URL").context("missing SUPABASE_URL")?;
    let key = env::var("SUPABASE_KEY")
        .or_else(|_| env::var("SUPABASE_ANON_KEY"))
        .or_else(|_| env::var("SUPABASE_PUBLISHABLE_KEY"))
        .context("missing SUPABASE_KEY or SUPABASE_ANON_KEY")?;
    let device_id = env::var("DARTSNUT_DEVICE_ID").unwrap_or_else(|_| "unknown-device".to_string());
    Ok(SupabaseConfig { url, key, device_id })
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
    let url = format!("{}/rest/v1/rpc/apply_remote_device_patch", cfg.url.trim_end_matches('/'));
    client
        .post(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Content-Type", "application/json")
        .header("Prefer", "return=representation")
        .json(&json!({
            "p_device_id": cfg.device_id,
            "p_patch": patch,
            "p_full": full,
            "p_source": "supabase_bridge"
        }))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn fetch_state(client: &Client, cfg: &SupabaseConfig) -> Result<Option<Value>> {
    let url = format!(
        "{}/rest/v1/remote_devices?select=state&device_id=eq.{}&limit=1",
        cfg.url.trim_end_matches('/'),
        cfg.device_id
    );
    let rows = client
        .get(url)
        .header("apikey", &cfg.key)
        .header("Authorization", format!("Bearer {}", cfg.key))
        .header("Accept", "application/json")
        .send()?
        .error_for_status()?
        .json::<Vec<DeviceRow>>()?;
    Ok(rows.into_iter().next().map(|r| r.state))
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
    let client_clone = client.clone();
    thread::spawn(move || {
        let mut last_state = Value::Null;
        let mut sent_initial = false;
        loop {
            match fetch_state(&client_clone, &cfg_clone) {
                Ok(Some(state)) => {
                    if state != last_state {
                        let kind = if sent_initial { "config" } else { "config_initial" };
                        if send_msg(&writer_clone, kind, state.clone()).is_ok() {
                            last_state = state;
                            sent_initial = true;
                        }
                    }
                    let _ = send_msg(&writer_clone, "bridge_health", json!({ "state": "connected" }));
                }
                Ok(None) => {
                    let _ = send_msg(&writer_clone, "bridge_health", json!({ "state": "connected" }));
                }
                Err(_) => {
                    let _ = send_msg(&writer_clone, "bridge_health", json!({ "state": "disconnected" }));
                }
            }
            thread::sleep(Duration::from_secs(2));
        }
    });

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
                let _ = rpc_apply_patch(&client, &cfg, msg.payload, true);
            }
            "device_state" => {
                let _ = rpc_apply_patch(&client, &cfg, msg.payload, false);
            }
            _ => {}
        }
    }
    Ok(())
}
