"""Build a credential-free Alibaba FC Web Function ZIP on Linux x86_64/Python 3.10.

This script only packages code, dependencies and a public RDS CA certificate.
It never reads an account database config, payment key or Aliyun credential.
Deployment and database migration are separate, explicit operator actions.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ca-file", type=Path, required=True,
                        help="Public ApsaraDB CA PEM; never pass a private key")
    parser.add_argument("--output", type=Path, required=True,
                        help="New ZIP path; an existing file is never overwritten")
    args = parser.parse_args()

    if not (sys.platform == "linux" and sys.version_info[:2] == (3, 10)
            and platform.machine().lower() in {"x86_64", "amd64"}):
        parser.error("FC ZIP must be built on Linux x86_64 with Python 3.10")
    ca = args.ca_file.expanduser().resolve(strict=True)
    ca_bytes = ca.read_bytes()
    pem_labels = re.findall(rb"-----BEGIN ([A-Z ]+)-----", ca_bytes)
    pem_remainder = re.sub(rb"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----",
                           b"", ca_bytes).strip()
    if (ca.suffix.lower() != ".pem" or not pem_labels
            or set(pem_labels) != {b"CERTIFICATE"} or pem_remainder
            or len(ca_bytes) > 1024 * 1024):
        parser.error("--ca-file must be a public PEM CA certificate")
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".zip" or output.exists():
        parser.error("--output must be a new .zip path")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="jnp-fc-build-") as temporary:
        stage = Path(temporary) / "code"
        stage.mkdir()
        subprocess.run([
            sys.executable, "-m", "pip", "--isolated", "install",
            "--index-url", "https://pypi.org/simple", "--no-cache-dir",
            "--target", str(stage), "-r", str(ROOT / "requirements.txt"),
        ], check=True)
        package = stage / "junengpao_cloud"
        if package.exists():
            raise RuntimeError("Dependency package unexpectedly contains our application name")
        package.mkdir()
        for source in (ROOT / "junengpao_cloud").glob("*.py"):
            shutil.copy2(source, package / source.name)
        certs = stage / "certs"
        certs.mkdir()
        shutil.copy2(ca, certs / "rds-ca.pem")

        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for source in sorted(stage.rglob("*")):
                if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
                    archive.write(source, source.relative_to(stage).as_posix())

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"FC ZIP: {output}")
    print(f"SHA256: {digest}")
    print("Contains public CA only. No database password, AccessKey or payment key is packaged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
