---

## title: Sideload and Launch a Game

nav_order: 3

# Sideload a Game to `apps/` and Launch It

This guide shows how to:

1. SSH into the machine
2. Copy a new game into `apps/`
3. Launch it from the machine UI
4. Read game logs
5. Avoid common mistakes that prevent launch

## 1) SSH into the machine

From your computer:

```bash
ssh rpi@<machine-ip>
```

Then move to the project root:

```bash
cd /home/rpi/dartsnut_rpi
```

## 2) Confirm runtime is healthy

The game launcher runs inside `dartsnut_python.service`. Check it first:

```bash
sudo systemctl status dartsnut_python.service
```

If needed:

```bash
sudo systemctl restart dartsnut_python.service
```

## 3) Prepare game folder structure

Each game must live under:

```text
/home/rpi/dartsnut_rpi/apps/<game_id>/
```

Minimum required files:

- `apps/<game_id>/main.py`
- `apps/<game_id>/conf.json`

Example `conf.json`:

```json
{
  "id": "mygame",
  "type": "game",
  "name": "My Game",
  "version": "1.0.0",
  "description": "My sideloaded game",
  "size": [128, 160],
  "fields": [],
  "preview": []
}
```

## 4) Sideload files into `apps/`

### Option A: copy from USB/local file on machine

```bash
cp -r "/path/to/mygame" "/home/rpi/dartsnut_rpi/apps/mygame"
```

### Option B: copy from your computer with `scp`

Run this on your computer (not on the Pi):

```bash
scp -r ./mygame rpi@<machine-ip>:/home/rpi/dartsnut_rpi/apps/mygame
```

## 5) Validate before launch

On the machine:

```bash
cd /home/rpi/dartsnut_rpi
test -f apps/mygame/main.py && echo "main.py OK"
test -f apps/mygame/conf.json && echo "conf.json OK"
python -m json.tool apps/mygame/conf.json >/dev/null && echo "conf.json valid JSON"
```

Optional quick check (same interpreter family as machine runtime):

```bash
cd /home/rpi/dartsnut_rpi/apps/mygame
sudo /root/.local/bin/uv run --directory /home/rpi/dartsnut_rpi python ./main.py --shm game_shm --data-store /tmp/mygame_test.json
```

If your game exits with argument errors, update its CLI parser to accept:

- `--shm`
- `--data-store`

## 6) Launch from the machine

After sideloading:

1. On device, go to **Games** from the main menu.
2. Find your game in the game list.
3. Press **A** to launch.

Machine UI launch behavior:

- The runtime launches your game as a subprocess using:
  - executable: `/root/.local/bin/uv run --directory /home/rpi/dartsnut_rpi python`
  - script: `main.py` (relative to `apps/<game_id>/`)
  - working directory (`cwd`): `apps/<game_id>`

Because `cwd` is `apps/<game_id>`, launching `main.py` from other locations may
change import/path resolution and can cause failures (especially relative imports
or relative file access).

If the game does not appear:

- verify `type` is exactly `"game"` in `conf.json`
- verify `conf.json` is valid JSON
- verify folder exists under `apps/`

## 7) Pure game logs (direct terminal run)

Use this mode when you want clean logs from only your game process.

1. Stop the main service first:

```bash
sudo systemctl stop dartsnut_python.service
```

1. Run game directly with uv:

```bash
cd /home/rpi/dartsnut_rpi/apps/mygame
sudo /root/.local/bin/uv run --directory /home/rpi/dartsnut_rpi python ./main.py --shm game_shm --data-store /tmp/mygame_dev.json
```

1. Read logs/errors directly in the same terminal (stdout/stderr).
2. After direct testing, bring normal machine runtime back:

```bash
sudo systemctl start dartsnut_python.service
```

## 8) Service logs (UI launch path)

The Python service captures stdout/stderr (including launched games) in journald.

### Live logs

```bash
journalctl -u dartsnut_python.service -f
```

### Recent logs

```bash
journalctl -u dartsnut_python.service -e
```

### Filter for your game ID

```bash
journalctl -u dartsnut_python.service --since "30 min ago" --grep "game_id=mygame"
```

You should see entries similar to:

- `game process started game_id=mygame pid=...`
- `in_game: game process exited game_id=mygame`

## Do and Don't Checklist

### Do

- Do keep game code in `apps/<game_id>/`.
- Do set `"type": "game"` in `conf.json`.
- Do keep `"id"` stable and aligned with your game identity.
- Do make sure `main.py` exists and starts with the environment Python.
- Do log useful startup/runtime errors to stdout/stderr.
- Do restart `dartsnut_python.service` after major game updates.
- Do use `sudo /root/.local/bin/uv run --directory /home/rpi/dartsnut_rpi python` for local/direct game runs.

### Don't

- Don't place the game outside `apps/` and expect discovery.
- Don't ship invalid JSON in `conf.json`.
- Don't omit required launch args (`--shm`, `--data-store`) in your game parser.
- Don't run direct game testing while `dartsnut_python.service` is still running.
- Don't assume relative paths from repo root; runtime `cwd` is `apps/<game_id>/`.
- Don't block forever before first frame/update without logging.
- Don't remove or rename `main.py` after sideloading.

## Quick troubleshooting

1. **Game not listed in menu**
  - `conf.json` missing/invalid
  - `"type"` is not `"game"`
2. **Game listed but fails to start**
  - `main.py` missing
  - parser rejects `--shm` / `--data-store`
  - runtime exception at startup (check journald logs)
  - wrong Python invocation (use `sudo /root/.local/bin/uv run --directory /home/rpi/dartsnut_rpi python` for direct runs)
3. **No useful logs**
  - ensure game prints/logs to stdout/stderr
  - use `journalctl -u dartsnut_python.service -f` while launching

