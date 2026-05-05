"""
JARVIS â€” Secret manager (Windows Credential Manager front-end)
================================================================
Move sensitive keys (ANTHROPIC_API_KEY, GROQ_API_KEY) out of the
plaintext .env file and into the encrypted Windows Credential Vault.

Once migrated, secrets are stored encrypted-at-rest by Windows. Jarvis's
config.py reads them automatically on startup. The .env file becomes
optional for these keys.

Usage (from C:\\Claude\\Jarvis):

    .venv\\Scripts\\python.exe deploy\\manage_secrets.py migrate
        Move all sensitive keys from .env into the Credential Vault.
        Original .env lines stay (commented out, for reference).

    .venv\\Scripts\\python.exe deploy\\manage_secrets.py list
        Show which keys are stored in the vault (values are NEVER printed).

    .venv\\Scripts\\python.exe deploy\\manage_secrets.py set <KEY>
        Interactively set a single key (e.g. set ANTHROPIC_API_KEY).

    .venv\\Scripts\\python.exe deploy\\manage_secrets.py delete <KEY>
        Remove a key from the vault (Jarvis will fall back to .env).
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

try:
    import keyring
except ImportError:
    print("ERROR: 'keyring' not installed. Run:")
    print("  C:\\Claude\\Jarvis\\.venv\\Scripts\\pip.exe install keyring")
    sys.exit(1)

SERVICE = "Jarvis"
SUPPORTED_KEYS = ("ANTHROPIC_API_KEY", "GROQ_API_KEY")
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"


def _print_header() -> None:
    print()
    print("  +------------------------------------------+")
    print("  |  JARVIS - Secret Manager                 |")
    print("  |  Backend: Windows Credential Vault       |")
    print("  +------------------------------------------+")
    print()


def cmd_list() -> int:
    _print_header()
    print(f"  Service: {SERVICE}")
    print()
    found_any = False
    for key in SUPPORTED_KEYS:
        try:
            val = keyring.get_password(SERVICE, key)
        except Exception as e:
            print(f"  {key:<30} â€” vault read error: {e}")
            continue
        if val:
            preview = val[:8] + "â€¦" + val[-4:] if len(val) > 14 else "***"
            print(f"  âœ“ {key:<28} stored ({preview})")
            found_any = True
        else:
            print(f"  âœ— {key:<28} not set in vault")
    print()
    if not found_any:
        print("  No keys in vault yet â€” run 'migrate' to import from .env.")
    print()
    return 0


def cmd_migrate() -> int:
    _print_header()
    if not ENV_PATH.exists():
        print(f"  ERROR: .env not found at {ENV_PATH}")
        return 2

    # Parse .env (without using dotenv) so we can show the user exactly what we found
    env_vals: dict[str, str] = {}
    raw_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        env_vals[k.strip()] = v.strip().strip('"').strip("'")

    moved: list[str] = []
    skipped: list[str] = []
    for key in SUPPORTED_KEYS:
        v = env_vals.get(key, "")
        if not v or v in ("not-set", ""):
            skipped.append(f"{key} (no value in .env)")
            continue
        try:
            keyring.set_password(SERVICE, key, v)
            moved.append(key)
        except Exception as e:
            print(f"  âœ— {key} â€” vault write FAILED: {e}")

    print(f"  Migrated to vault:  {len(moved)}")
    for k in moved:
        print(f"    âœ“ {k}")
    if skipped:
        print(f"  Skipped:")
        for s in skipped:
            print(f"    Â- {s}")

    if moved:
        print()
        print("  â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
        print(f"  Done. Your secrets are now encrypted in the Windows Credential Vault.")
        print(f"  You can SAFELY remove the migrated lines from .env (or leave them â€” vault wins).")
        print(f"  Jarvis will read the vault first on every startup.")
        print()
        print("  Verify with:  python deploy/manage_secrets.py list")
    print()
    return 0


def cmd_set(key: str) -> int:
    _print_header()
    if key not in SUPPORTED_KEYS:
        print(f"  ERROR: unknown key '{key}'. Supported: {', '.join(SUPPORTED_KEYS)}")
        return 2
    val = getpass.getpass(f"  Enter value for {key} (input hidden): ").strip()
    if not val:
        print("  Empty value â€” nothing changed.")
        return 1
    try:
        keyring.set_password(SERVICE, key, val)
        print(f"  âœ“ Stored {key} in Windows Credential Vault.")
        return 0
    except Exception as e:
        print(f"  âœ— FAILED: {e}")
        return 1


def cmd_delete(key: str) -> int:
    _print_header()
    if key not in SUPPORTED_KEYS:
        print(f"  ERROR: unknown key '{key}'. Supported: {', '.join(SUPPORTED_KEYS)}")
        return 2
    try:
        keyring.delete_password(SERVICE, key)
        print(f"  âœ“ Removed {key} from vault. Jarvis will fall back to .env.")
        return 0
    except keyring.errors.PasswordDeleteError:
        print(f"  Â- {key} was not in vault (nothing to delete).")
        return 0
    except Exception as e:
        print(f"  âœ— FAILED: {e}")
        return 1


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    cmd = args[0].lower()
    if cmd == "migrate":
        return cmd_migrate()
    if cmd == "list":
        return cmd_list()
    if cmd == "set":
        if len(args) < 2:
            print("Usage: manage_secrets.py set <KEY>")
            return 2
        return cmd_set(args[1].upper())
    if cmd == "delete":
        if len(args) < 2:
            print("Usage: manage_secrets.py delete <KEY>")
            return 2
        return cmd_delete(args[1].upper())
    print(f"Unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
