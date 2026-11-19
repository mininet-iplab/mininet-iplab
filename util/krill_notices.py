#!/usr/bin/env python3
"""Regenerate the licence notices for the Krill binaries the image installs.

The lab image installs Krill's own builds, unmodified: NLnet Labs' Debian
package on amd64, and on arm64 the static ``krill`` and ``krillc`` from the
official ``nlnetlabs/krill`` image. Both are built from the same tag, so they
link the same Rust crates, and the image must carry Krill's own licence
(MPL-2.0) and the licences of those crates. Neither the package nor the
image ships the crate notices.

This script reads Krill's ``Cargo.lock`` at the pinned tag, downloads each
registry crate's published ``.crate`` archive from crates.io (source only;
nothing is compiled), and writes:

- ``licenses/krill/LICENSE`` -- Krill's MPL-2.0 text at that tag
- ``licenses/krill/THIRD_PARTY_CRATES.md`` -- every crate in the lock file,
  its declared licence, and the licence and notice files it publishes,
  with identical texts printed once.
- ``licenses/krill/RUNTIME.md`` -- the C runtime linked into the binaries.
  The amd64 package links Debian's own libraries, which carry their own
  notices. The arm64 binaries are static: OpenSSL and musl are built into
  them, so their licence texts are reproduced. The OpenSSL version is read
  from the arm64 ``krill`` binary with ``docker``.

``Cargo.lock`` lists every crate any target or feature could use, so the
notices are a superset of what one binary links. That errs on the side of
attribution.

Run it after bumping Krill in the ``Dockerfile``, and commit the result in
the same change::

    python3 util/krill_notices.py v0.16.1 sha256:<index digest>
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

RAW = "https://raw.githubusercontent.com/NLnetLabs/krill/{tag}/{path}"
CRATE = "https://static.crates.io/crates/{name}/{name}-{version}.crate"
LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice|copyright|authors)", re.I)
# musl does not record its version in a static binary. Its licence text is
# reproduced from this release; it is identical in 1.2.3 through 1.2.5 (1.2.0
# lacks the AArch64 copyright lines, so do not go back before 1.2.3).
MUSL_TEXT_VERSION = "1.2.5"
MUSL_TEXT = "https://git.musl-libc.org/cgit/musl/plain/COPYRIGHT?h=v{v}"
OPENSSL_TEXT = "https://raw.githubusercontent.com/openssl/openssl/openssl-{v}/LICENSE.txt"
OUT_DIR = Path(__file__).resolve().parent.parent / "licenses" / "krill"


def fetch(url: str) -> bytes:
    """Download one URL and return its body.

    Args:
        url: The URL to fetch.

    Returns:
        The response body.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "mininet-iplab-notices"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def crate_licence(name: str, version: str) -> tuple[str, list[tuple[str, str]]]:
    """Read one crate's declared licence and its licence files.

    Args:
        name: The crate name.
        version: The exact crate version from ``Cargo.lock``.

    Returns:
        The ``license`` (or ``license-file``) value from its ``Cargo.toml``,
        and ``(file name, text)`` for each top-level licence or notice file.
    """
    data = fetch(CRATE.format(name=name, version=version))
    declared = "unstated"
    files: list[tuple[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if len(parts) != 2 or not member.isfile():
                continue
            fobj = tar.extractfile(member)
            if fobj is None:
                continue
            if parts[1] == "Cargo.toml":
                package = tomllib.loads(fobj.read().decode()).get("package", {})
                declared = package.get("license") or (
                    f"see {package['license-file']}" if "license-file" in package else declared
                )
            elif LICENCE_FILE.match(parts[1]):
                text = fobj.read().decode("utf-8", errors="replace").strip()
                files.append((parts[1], text))
    return declared, sorted(files)


def arm64_openssl_version(image: str) -> str:
    """Read the OpenSSL version built into the arm64 ``krill`` binary.

    Args:
        image: The pinned ``nlnetlabs/krill`` image reference.

    Returns:
        The version, for example ``3.6.3``.

    Raises:
        RuntimeError: If the binary names no OpenSSL version.
    """
    cid = subprocess.run(
        ["docker", "create", "--platform", "linux/arm64", image],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    try:
        archive = subprocess.run(
            ["docker", "cp", f"{cid}:/usr/local/bin/krill", "-"],
            check=True, capture_output=True,
        ).stdout
    finally:
        subprocess.run(["docker", "rm", cid], check=True, capture_output=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        member = tar.extractfile("krill")
        binary = member.read() if member else b""
    found = re.search(rb"OpenSSL (\d+\.\d+\.\d+) ", binary)
    if not found:
        raise RuntimeError("the arm64 krill binary names no OpenSSL version")
    return found.group(1).decode()


def runtime_notices(tag: str, digest: str) -> str:
    """Describe the C runtime linked into each architecture's binaries.

    Args:
        tag: The Krill image tag.
        digest: The image's multi-arch index digest, as pinned in the Dockerfile.

    Returns:
        The Markdown text of ``RUNTIME.md``.
    """
    image = f"nlnetlabs/krill:{tag}@{digest}"
    openssl = arm64_openssl_version(image)
    out = [
        f"# Krill {tag}: the runtime linked into the binaries",
        "",
        "**amd64.** The lab image installs NLnet Labs' Debian package of Krill",
        "from https://packages.nlnetlabs.nl/. Its `krill` and `krillc` are linked",
        "dynamically against Debian's own `libc6` and `libssl3`, whose notices are",
        "installed with those packages under `/usr/share/doc/<package>/copyright`.",
        "",
        f"**arm64.** The lab image copies the static `krill` and `krillc` from",
        f"`{image}`. Built into them are:",
        "",
        "| Component | Version | Licence |",
        "|---|---|---|",
        f"| OpenSSL | {openssl} | Apache-2.0 |",
        "| musl | not recorded in the binary | MIT |",
        "| Compiler runtime support (LLVM's libunwind, GCC's libgcc) | not recorded"
        " | Apache-2.0 WITH LLVM-exception; GPL-3.0 with the GCC Runtime Library Exception |",
        "",
        "Both runtime exceptions let a linked binary carry that code without",
        "reproducing its licence. The OpenSSL and musl texts follow; musl's is",
        f"reproduced from its {MUSL_TEXT_VERSION} release, and is identical in 1.2.3",
        "through 1.2.5. Sources: https://www.openssl.org/ and https://musl.libc.org/.",
    ]
    for project, url in (
        (f"openssl {openssl}", OPENSSL_TEXT.format(v=openssl)),
        (f"musl {MUSL_TEXT_VERSION}", MUSL_TEXT.format(v=MUSL_TEXT_VERSION)),
    ):
        out += ["", f"## {project}: {url.rsplit('/', 1)[-1]}",
                "", "```text", fetch(url).decode().strip(), "```"]
    return "\n".join(out) + "\n"


def main() -> int:
    """Write Krill's licence and the crate notices for one Krill tag.

    Returns:
        The process exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the Krill git tag, e.g. v0.16.1")
    parser.add_argument("digest", help="the image's multi-arch index digest (sha256:...)")
    args = parser.parse_args()

    lock = tomllib.loads(fetch(RAW.format(tag=args.tag, path="Cargo.lock")).decode())
    packages = [p for p in lock["package"] if p.get("source", "").startswith("registry+")]
    local = sorted(p["name"] for p in lock["package"] if "source" not in p)

    texts: dict[str, str] = {}
    users: dict[str, list[str]] = {}
    rows: list[str] = []
    for pkg in sorted(packages, key=lambda p: (p["name"], p["version"])):
        label = f"{pkg['name']} {pkg['version']}"
        print(label, file=sys.stderr)
        declared, files = crate_licence(pkg["name"], pkg["version"])
        rows.append(f"| {pkg['name']} | {pkg['version']} | {declared} |")
        if not files:
            users.setdefault("", []).append(label)
        for fname, text in files:
            digest = hashlib.sha256(text.encode()).hexdigest()[:12]
            texts.setdefault(digest, text)
            users.setdefault(digest, []).append(f"{label} ({fname})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "LICENSE").write_bytes(fetch(RAW.format(tag=args.tag, path="LICENSE")))

    out = [
        f"# Krill {args.tag}: third-party crates",
        "",
        "The `krill` and `krillc` binaries in the lab image are Krill's own builds,",
        "unmodified: NLnet Labs' Debian package on amd64, and on arm64 the static",
        f"binaries from `nlnetlabs/krill:{args.tag}`. Krill itself is MPL-2.0 (see `LICENSE`",
        f"beside this file); its source is https://github.com/NLnetLabs/krill/tree/{args.tag}.",
        "Both builds statically link the Rust crates below. This list is generated",
        f"from Krill's `Cargo.lock` at {args.tag} by `util/krill_notices.py`, so it",
        "covers every crate the lock file names, a superset of what one binary links.",
        "",
        f"Workspace crates (part of Krill): {', '.join(local)}.",
        "",
        "| Crate | Version | Licence |",
        "|---|---|---|",
        *rows,
        "",
        "## Licence and notice texts",
        "",
        "Each text below is reproduced from the crates' published archives;",
        "crates that publish the same text share one copy.",
    ]
    for digest, text in texts.items():
        out += ["", "### " + ", ".join(users[digest]), "", "```text", text, "```"]
    if "" in users:
        out += [
            "",
            "### Crates that publish no licence file",
            "",
            "These declare their licence only in `Cargo.toml` (see the table above):",
            ", ".join(users[""]) + ".",
        ]
    (OUT_DIR / "THIRD_PARTY_CRATES.md").write_text("\n".join(out) + "\n")
    (OUT_DIR / "RUNTIME.md").write_text(runtime_notices(args.tag, args.digest))
    print(f"wrote {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
