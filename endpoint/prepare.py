"""Build-machine source setup. No Python is installed on the endpoint."""
import hashlib
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(dest, *args):
    return subprocess.check_output(["git", "-C", str(dest), *args], text=True).strip()


def main():
    sources = tomllib.loads((ROOT / "endpoint/sources.toml").read_text())
    for name, spec in sources.items():
        rev, url = spec["rev"], spec["git"]
        if not re.fullmatch(r"[0-9a-f]{40}", rev):
            raise ValueError(f"not an exact revision: {name}")
        dest = ROOT / ".deps" / name
        if not dest.exists():
            dest.mkdir(parents=True)
            git(dest, "init", "-q")
            git(dest, "remote", "add", "origin", url)
            git(dest, "fetch", "--depth=1", "origin", rev)
            git(dest, "checkout", "--detach", "FETCH_HEAD")
        if git(dest, "rev-parse", "HEAD") != rev:
            raise ValueError(f"wrong revision: {dest}")
        if git(dest, "remote", "get-url", "origin") != url:
            raise ValueError(f"wrong remote: {dest}")
        if git(dest, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError(f"dirty dependency: {dest}")
    lock = (ROOT / ".deps/ezkl/Cargo.lock").read_bytes()
    if hashlib.sha256(lock).hexdigest() != "4cccdabde37ed7afeffa4e654c61c7302cea241619c6f80da3aa2768bfcaf49c":
        raise ValueError("EZKL upstream lock changed")
    print("pinned sources checked")


if __name__ == "__main__":
    main()
