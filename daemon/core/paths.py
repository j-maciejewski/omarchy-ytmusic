"""Runtime paths configuration isolated to user runtime directory."""

import os
from pathlib import Path


def get_runtime_dir() -> Path:
    """
    Return secure, user-isolated runtime directory following XDG Base Directory Specification.
    Defaults to $XDG_RUNTIME_DIR/omarchy-ytmusic or ~/.local/state/omarchy-ytmusic/run
    with 0700 permissions.
    """
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and os.path.isdir(xdg_runtime):
        base = Path(xdg_runtime) / "omarchy-ytmusic"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
        base = Path(xdg_state) / "omarchy-ytmusic" / "run"

    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except Exception:
        pass
    return base


def get_state_dir() -> Path:
    """Return the private directory for settings that must survive reboots (~/.local/state/omarchy-ytmusic)."""
    xdg_state = os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))
    base = Path(xdg_state) / "omarchy-ytmusic"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except Exception:
        pass
    return base


RUNTIME_DIR = get_runtime_dir()
STATE_DIR = get_state_dir()
SETTINGS_FILE = str(STATE_DIR / "settings.json")
SOCKET_PATH = str(RUNTIME_DIR / "daemon.sock")
MPV_SOCKET_PATH = str(RUNTIME_DIR / "mpv.sock")
PID_FILE = str(RUNTIME_DIR / "daemon.pid")
START_LOCK_FILE = str(RUNTIME_DIR / "daemon-start.lock")
LOG_FILE = str(RUNTIME_DIR / "daemon.log")
COOKIE_FILE = str(RUNTIME_DIR / "cookies.txt")
