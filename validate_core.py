"""Mihomo group loading smoke test for the public template using only synthetic groups."""
from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path
import subprocess
import tempfile
import urllib.request

import yaml
from template_sync import CORE_GROUPS, read_yaml, validate_routes

VERSION = "v1.19.31"
ARCHIVE_SHA256 = "04cf9f09671704f839ddbee2e93069dc831a4123a75281e725d1d96ab9ac1afc"
BINARY_SHA256 = "b341a765412c192685264e038a6aad2ac1c67c12b8aceeb5c6f64955cf43f5ed"
CORE_URL = f"https://github.com/MetaCubeX/mihomo/releases/download/{VERSION}/mihomo-linux-amd64-compatible-{VERSION}.gz"


def check_template(path: Path, core: Path | None):
    data = read_yaml(path.read_bytes())
    validate_routes(data, public=True)
    present = {group["name"] for group in data["proxy-groups"]}
    data["proxy-groups"] = [
        {"name": name, "type": "select", "proxies": ["DIRECT"]}
        for name in CORE_GROUPS if name not in present
    ] + data["proxy-groups"]
    # Full rule/provider references were checked above. This core invocation
    # checks generated group loading without needing provider/geodata downloads.
    data["proxies"] = []
    data["rules"] = ["MATCH,DIRECT"]
    data.pop("rule-providers", None)
    data.pop("sub-rules", None)
    for key in ("geox-url", "geo-auto-update", "geo-update-interval", "geodata-mode"):
        data.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="template-check-") as name:
        root = Path(name)
        if core is None:
            with urllib.request.urlopen(CORE_URL, timeout=30) as response:
                archive = response.read(48 * 1024 * 1024 + 1)
            if hashlib.sha256(archive).hexdigest() != ARCHIVE_SHA256:
                raise ValueError("Mihomo archive checksum mismatch")
            binary = gzip.decompress(archive)
            if hashlib.sha256(binary).hexdigest() != BINARY_SHA256:
                raise ValueError("Mihomo binary checksum mismatch")
            core = root / "mihomo"
            core.write_bytes(binary)
            core.chmod(0o700)
        config = root / "config.yaml"
        config.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        proc = subprocess.run([str(core), "-t", "-d", str(root), "-f", str(config)],
                              capture_output=True, timeout=60)
        if proc.returncode:
            raise ValueError("Mihomo failed to load synthetic routing groups")
    print("public route references and synthetic Mihomo groups: OK")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--file", type=Path, default=Path(__file__).with_name("template.yaml"))
    cli.add_argument("--core", type=Path)
    args = cli.parse_args()
    check_template(args.file, args.core)
