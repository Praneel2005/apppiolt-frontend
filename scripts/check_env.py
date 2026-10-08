"""Cross-platform environment check. Run:  python scripts/check_env.py
Prints OK / MISSING for each tool the project needs and whether port 5432 is free."""
import shutil
import socket
import subprocess
import sys


def run(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def check(name, cmd, exe=None, min_major=None, min_minor=None):
    path = shutil.which(exe or cmd[0])
    if not path:
        print(f"MISSING  {name}")
        return False
    ver = run(cmd)
    print(f"OK       {name}: {ver}")
    return True


ok = True
print(f"Python   : {sys.version.split()[0]}  (3.11+ required, 3.12 preferred)")
ok &= sys.version_info >= (3, 11)
ok &= check("git", ["git", "--version"])
ok &= check("node (need 20+)", ["node", "--version"])
ok &= check("npm", ["npm", "--version"])
ok &= check("docker", ["docker", "--version"])
dc = check("docker compose", ["docker", "compose", "version"], exe="docker")
ok &= dc

s = socket.socket()
s.settimeout(1)
busy = s.connect_ex(("127.0.0.1", 5432)) == 0
s.close()
print(("WARN     port 5432 is already in use (another Postgres?). Change the port in docker-compose.yml or stop it."
       if busy else "OK       port 5432 is free"))
print("\nALL GOOD" if ok else "\nFix the MISSING items above before continuing.")
sys.exit(0 if ok else 1)
