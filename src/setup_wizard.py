"""First-run interactive setup and PalServer auto-detection."""

import os
import re
import shutil
import socket

import yaml

from src.settings import DEFAULT_SECURITY_SETTINGS


def auto_detect_palserver():
    """Try to find PalServer.exe in common locations. Returns path or None."""
    for name in ["PalServer-Win64-Shipping-Cmd.exe", "PalServer.exe"]:
        found = shutil.which(name)
        if found:
            return found

    candidates = []

    # Check Steam registry for install path
    try:
        import winreg

        for root in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Valve\Steam")
                steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
                steam_path = steam_path.replace("/", "\\")
                candidates.append(
                    os.path.join(steam_path, "steamapps", "common", "PalServer", "PalServer.exe")
                )
                winreg.CloseKey(key)
                break
            except OSError:
                pass
    except Exception:
        pass

    # Common install paths
    pf_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", "C:\\Program Files")
    candidates.extend([
        os.path.join(pf_x86, "Steam", "steamapps", "common", "PalServer", "PalServer.exe"),
        os.path.join(pf, "PalServer", "PalServer.exe"),
    ])

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _palserver_install_roots(exe_path):
    """Candidate directories that may contain the actual PalServer game files.

    Normally that's next to the exe itself. Under LinuxGSM, `exe_path` is
    the instance management script (e.g. `~/pwserver/pwserver`), and the
    real game files live in `~/pwserver/serverfiles/`.
    """
    exe_dir = os.path.dirname(exe_path)
    return [exe_dir, os.path.join(exe_dir, "serverfiles")]


def _find_palserver_ini(exe_path):
    """Locate or create PalWorldSettings.ini. Returns path or None."""
    roots = _palserver_install_roots(exe_path)

    for root in roots:
        config_base = os.path.join(root, "Pal", "Saved", "Config")
        for platform_dir in ("WindowsServer", "LinuxServer"):
            candidate = os.path.join(config_base, platform_dir, "PalWorldSettings.ini")
            if os.path.isfile(candidate):
                return candidate

    for root in roots:
        default_ini = os.path.join(root, "DefaultPalWorldSettings.ini")
        if os.path.isfile(default_ini):
            ini_path = os.path.join(root, "Pal", "Saved", "Config", "WindowsServer", "PalWorldSettings.ini")
            os.makedirs(os.path.dirname(ini_path), exist_ok=True)
            shutil.copy(default_ini, ini_path)
            return ini_path

    return None


def read_palserver_admin_password(exe_path):
    """Read AdminPassword from PalWorldSettings.ini. Returns '' if not found."""
    ini_path = _find_palserver_ini(exe_path)
    if ini_path is None:
        return ""
    with open(ini_path, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'AdminPassword="([^"]*)"', content)
    return match.group(1) if match else ""


def configure_palserver_rest_api(exe_path, admin_password=None):
    """Enable REST API in PalWorldSettings.ini.

    Always sets RESTAPIEnabled=True and RESTAPIPort=8212.
    Only sets AdminPassword if *admin_password* is given and the current
    value is empty (won't overwrite an existing password).

    Returns True if the file was modified.
    """
    ini_path = _find_palserver_ini(exe_path)
    if ini_path is None:
        return False

    with open(ini_path, "r", encoding="utf-8") as f:
        content = f.read()

    updated = content
    updated = re.sub(r"RESTAPIEnabled=(True|False)", "RESTAPIEnabled=True", updated)
    updated = re.sub(r"RESTAPIPort=\d+", "RESTAPIPort=8212", updated)

    if admin_password:
        # Only set if current password is empty
        match = re.search(r'AdminPassword="([^"]*)"', updated)
        current = match.group(1) if match else ""
        if not current:
            updated = re.sub(
                r'AdminPassword="[^"]*"',
                f'AdminPassword="{admin_password}"',
                updated,
            )

    # Append missing REST API keys
    for key_value in ["RESTAPIEnabled=True", "RESTAPIPort=8212"]:
        key = key_value.split("=")[0]
        if key not in updated:
            updated = re.sub(
                r"(OptionSettings=\(.*?)\)",
                rf"\1,{key_value})",
                updated,
                flags=re.DOTALL,
            )

    if admin_password and "AdminPassword=" not in updated:
        updated = re.sub(
            r"(OptionSettings=\(.*?)\)",
            rf'\1,AdminPassword="{admin_password}")',
            updated,
            flags=re.DOTALL,
        )

    if updated != content:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    return False


def _default_config(exe_path, admin_pass, web_pass, use_lgsm=False):
    """Return the default settings dict for a new installation."""
    return {
        "palserver": {
            "exePath": exe_path,
            "host": "127.0.0.1",
            "port": 8211,
            "adminPassword": admin_pass,
            "useLGSM": use_lgsm,
            "protocol": "REST",
            "restPort": 8212,
            "pollingRate": 5,
        },
        "web": {
            "enabled": True,
            "port": 8213,
            "username": "admin",
            "password": web_pass,
            "controlServer": True,
            "showServerIP": False,
        },
        "features": {
            "playerTracking": True,
            "autoStart": True,
            "autoStop": True,
        },
        "autoStop": {
            "stopDelay": 600,
        },
        "security": dict(DEFAULT_SECURITY_SETTINGS),
    }


def interactive_setup(settings_path, detected_exe, use_lgsm=False):
    """First-run wizard: prompt for required settings and save settings.yaml."""
    print()
    print("  Welcome to Palworld Monitor!")
    print()

    # Exe path
    if detected_exe:
        print(f"  Found PalServer at: {detected_exe}")
        exe = input("  Press Enter to accept, or type a different path: ").strip()
        exe = exe or detected_exe
    else:
        exe = ""
        prompt = (
            "  Path to your LGSM instance script (e.g. /home/gameserver/pwserver/pwserver): "
            if use_lgsm
            else "  Path to PalServer.exe: "
        )
        while not exe:
            exe = input(prompt).strip()

    # Admin password
    existing_pass = read_palserver_admin_password(exe)
    if existing_pass:
        print("  Using existing PalServer admin password from PalWorldSettings.ini")
        admin_pass = existing_pass
    else:
        admin_pass = ""
        while not admin_pass:
            admin_pass = input("  Choose a PalServer admin password: ").strip()

    # Auto-configure PalServer REST API (password only written if INI had none)
    if configure_palserver_rest_api(exe, admin_pass):
        print()
        print("  Enabled REST API in PalWorldSettings.ini.")
        if not existing_pass:
            print("  Admin password set.")
        print("  Restart PalServer for the changes to take effect.")
        print()
    else:
        print()
        print("  Could not auto-configure PalWorldSettings.ini.")
        print("  Make sure REST API is enabled:")
        print("    RESTAPIEnabled=True, RESTAPIPort=8212, AdminPassword=<your password>")
        print()

    # Web password — reuse admin password if available and user accepts
    if existing_pass:
        print("  Reusing PalServer admin password for web UI.")
        web_pass = input(
            "  Press Enter to accept, or type a different web password: "
        ).strip()
        web_pass = web_pass or existing_pass
    else:
        web_pass = ""
        while not web_pass:
            web_pass = input("  Web admin password: ").strip()

    config = _default_config(exe, admin_pass, web_pass, use_lgsm)

    with open(settings_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    print(f"\n  Configuration saved to {settings_path}")
    print()


def print_web_link(port):
    """Print localhost and LAN links to the web admin UI."""
    print(f"\n  > Web admin UI: http://localhost:{port}")
    try:
        lan_ip = socket.gethostbyname(socket.gethostname())
        if lan_ip not in ("127.0.0.1", "0.0.0.0"):
            print(f"  > LAN:          http://{lan_ip}:{port}")
    except Exception:
        pass
    print()
