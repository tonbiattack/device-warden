# Device Warden

**Know which Android device is safe to use — before you touch it.**

Device Warden is a lightweight Windows tray app that coordinates Android devices and emulators between people and AI coding agents. It shows at a glance who holds a device, whether it is connected, and whether AI agents are allowed to claim it.

<p align="center">
  <img src="assets/app-icon.svg" width="96" alt="Device Warden icon">
</p>

```text
端末                ADB                利用可否              確保者
Redmi Pad SE        connected / usb    自分が使用可能        —
emulator-5554       connected / emu    AI使用中              OpenCode / ses_example…
Pixel 8             disconnected       自分が使用可能（ADB未接続）  —
```

## Why

A Windows machine usually has phones, tablets, and emulators attached to it. When several AI coding agents and a human share them, two problems appear quickly:

- **Collisions.** Two agents pick the same device, and one silently breaks the other's test run.
- **Guesswork.** A device that is unplugged is not necessarily free, and an ADB-connected device is not necessarily idle.

Device Warden gives each device one lease at a time, recorded in a local SQLite database, and exposes that state through a tray UI and a small authenticated CLI/HTTP API.

## Features

- **Lease broker** — atomic `claim`, `heartbeat`, `release`, `list`. One active lease per device, enforced by a database constraint rather than check-then-act.
- **Connection tracking** — `connected`, `disconnected`, `unauthorized`, `unknown`. Disconnecting or restarting never silently frees a lease; it moves to **needs attention**.
- **Three independent axes** — ADB connection, lease ownership, and AI policy (`ai_allowed` / `ai_denied`) never overwrite each other.
- **Human and AI ownership** — OpenCode sessions are identified by session ID with title, project, and working directory. Other tools (Codex, Command Code, …) appear by tool name while keeping a unique lease per worker.
- **OpenCode plugin** — claims carry the real session ID and heartbeat automatically every 30 seconds. Leases are still released explicitly, never on session end.
- **ADB mediation** — `adb` calls proxied through a lease pin the target with `-s <serial>` and allow only a fixed subcommand set.
- **Audit trail** — every claim, release, and forced release records its owner; view it with `device-warden audit` or the GUI's **履歴** button.
- **Tray-first** — colour-coded shield icon (available / yours / AI / needs attention), Windows sign-in autostart, single-instance behaviour that reopens the existing window.

## Requirements

| Requirement | Notes |
| --- | --- |
| Windows 11 (Windows 10 likely works) | Tray and autostart use the Windows API |
| Python 3.11+ | `py -m venv` is recommended |
| Android SDK Platform Tools (`adb`) on `PATH` | `adb devices -l` must work |
| Node.js 24+ | Only for the OpenCode plugin test |

## Install

```powershell
git clone https://github.com/tonbiattack/device-warden.git
cd device-warden
py -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

Start the broker, then the tray in another terminal:

```powershell
.\.venv\Scripts\device-warden-broker
.\.venv\Scripts\device-warden-tray
```

The tray starts the broker itself when one is not running, so in practice starting `device-warden-tray` is enough.

Closing the window moves it to the notification area; **終了** in the tray menu exits only the UI. The broker is stopped separately with `Ctrl+C`.

## Usage

### Tray and window

For each device the window shows its name, transport, ADB state, the most important availability label, the current holder, and when it was last seen. Names come from the device itself: a custom alias wins, then the marketing name (`Redmi Pad SE 8.7`), then brand plus model (`OPPO CPH2699`), so bare model codes stay a last resort. Actions are **自分が確保**, **解放**, **強制解放** (with a confirmation warning), **AI許可切替**, **名前変更**, **表示切替**, **削除**, **履歴**, and **自動起動**. The list refreshes every 10 seconds, and so does the tray icon.

### Availability labels

| Label | Meaning |
| --- | --- |
| **AI使用中** | An AI lease is active and the device is connected — avoid it |
| **自分が使用中** | You hold the lease |
| **自分が使用可能** | No lease, ADB connected |
| **自分が使用可能（ADB未接続）** | No lease, currently disconnected — physical use is not guaranteed |
| **接続切れ・要確認** | A lease exists but the connection dropped; the holder is preserved |

An AI-denied device stays usable by humans and is shown with a separate AI policy marker.

### Windows sign-in autostart

```powershell
device-warden-autostart install   # register
device-warden-autostart status    # check
device-warden-autostart remove    # unregister
```

The GUI's **自動起動** button offers the same toggle. Registration creates a shortcut in the current user's Startup folder (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Device Warden.lnk`); no administrator rights are needed. A second launch does not duplicate the app — it brings the existing window to the front. Unregistering does not stop a running instance.

## CLI

```powershell
device-warden list
device-warden claim --tool Codex                    # auto-select a free device
device-warden claim --tool Codex --device <device_id>
device-warden adb <lease_id> shell getprop
device-warden heartbeat <lease_id>
device-warden release <lease_id>
device-warden audit
```

Prefix commands with `.\.venv\Scripts\` when the virtual environment is not activated. `--tool "Command Code"` works with any tool name.

The returned `lease_id` is the proof of ownership: keep it private, send a heartbeat at least every 120 seconds while working, and release when done. Lease IDs of AI leases are deliberately omitted from `list` output.

## OpenCode integration

Add the plugin directory to `plugins` in your OpenCode configuration:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["C:/apps/device-warden/integrations/opencode"]
}
```

The plugin registers a `device_warden` tool with `list`, `claim`, `heartbeat`, `release`, and `adb` actions. On `claim` it supplies the calling session ID, title, project, and working directory, and then heartbeats every 30 seconds until the lease is released. If the broker is unreachable it reports an actionable error instead of retrying silently.

Requirements for the integration to be meaningful:

- The session must claim a device **before** running ADB.
- `release` is explicit; session exit does not free the device.
- Direct `adb` calls outside the broker are outside Device Warden's control.

## How it works

```text
┌──────────────┐   authenticated localhost HTTP   ┌──────────────────┐
│ Tray / CLI   │ ───────────────────────────────► │ Broker (127.0.0.1)│
│ OpenCode     │ ◄─────────────────────────────── │ SQLite + ADB      │
└──────────────┘                                  └──────────────────┘
```

- `device_warden/store.py` — SQLite schema, lease transactions, ADB discovery and mediation.
- `device_warden/server.py` — localhost-only HTTP API with a bearer token, background monitor.
- `device_warden/gui.py` — Tkinter window, tray icon, state colours.
- `device_warden/autostart.py` — Startup shortcut, single-instance mutex, bring-to-front signal.
- `device_warden/icon.py` — icon artwork (the SVG in `assets/` is the canonical design).
- `integrations/opencode/` — OpenCode V2 plugin and its test.

Leases are released only by their own lease ID or by an explicit forced release. A disconnect, broker restart, or missed heartbeat moves a lease to `recovery_required` instead of freeing it. USB and Wi-Fi serials are treated as different devices on purpose, so a reconnect cannot be mistaken for a different unit.

Design documents: [specification](docs/SPEC.md) · [architecture](docs/ARCHITECTURE.md)

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEVICE_WARDEN_HOME` | `%USERPROFILE%\.device-warden` | Database, broker token, generated icon |

Data is stored outside `%LOCALAPPDATA%` on purpose: packaged Python installs may virtualise AppData, which would hide the broker token from the OpenCode plugin. The directory contains device serials and local paths, so keep it private and out of version control (`.gitignore` already excludes it).

## Tests

```powershell
python -m unittest discover -s tests -v
node integrations/opencode/test.mjs        # Node.js 24+
```

The suite covers parallel claims, policy enforcement, disconnect/reconnect ownership, stale heartbeat handling, ADB target pinning, broker authentication, audit output, icon state priority, and autostart install/remove.

## Limitations

Device Warden is a coordination tool, not a sandbox.

- **Direct ADB use cannot be detected or blocked.** Anyone or any agent that calls `adb` without going through the broker can still collide.
- **Connection means reachability, not idleness.** A connected device may be in use physically; a disconnected one may still be physically occupied.
- **Serial reuse or spoofing is not detected.** USB↔Wi-Fi switching and serial changes are treated as separate devices.
- **Local isolation is limited.** The API is localhost-only and token-authenticated, and AI lease IDs are hidden from listings, but any process running as the same Windows user can read the token and database.
- **No automatic release.** Idle detection or session end never frees a device, by design.

## Contributing

Issues and pull requests are welcome. Please:

1. Keep changes consistent with the state model in [docs/SPEC.md](docs/SPEC.md) — connection, lease, and AI policy are separate axes.
2. Never weaken the rule that a lost connection or stale lease must not silently become "free".
3. Add a test for behaviour changes; run the suites above.
4. Do not commit device serials, IPs, local paths, session names, or broker tokens.

## License

No license has been added yet. Until one is chosen, all rights are reserved by the author.
