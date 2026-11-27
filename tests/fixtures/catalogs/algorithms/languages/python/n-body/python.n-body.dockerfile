# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm

ARG HYPERFINE_VERSION=1.20.0
ARG HYPERFINE_SHA256_AMD64=e0391897ffb490af8a6b64e0e4281ac2c8e10cbcbb875bd22a376f44cdb263ab
ARG HYPERFINE_SHA256_ARM64=a469c7173d646b216405e29c18ba348b180ce214e4a580d63c6b81034fe2ca27

# Download with retries, check the pinned digest, install the engine.
RUN python3 - <<'PY' && dpkg -i /tmp/hyperfine.deb && rm /tmp/hyperfine.deb
import hashlib, os, subprocess, time, urllib.request
arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
want = {"amd64": os.environ["HYPERFINE_SHA256_AMD64"],
        "arm64": os.environ["HYPERFINE_SHA256_ARM64"]}[arch]
version = os.environ["HYPERFINE_VERSION"]
url = f"https://github.com/sharkdp/hyperfine/releases/download/v{version}/hyperfine_{version}_{arch}.deb"
for attempt in range(5):
    try:
        data = urllib.request.urlopen(url, timeout=60).read()
        break
    except OSError:
        time.sleep(2 ** attempt)
else:
    raise SystemExit("download failed: " + url)
assert hashlib.sha256(data).hexdigest() == want, "hyperfine checksum mismatch"
open("/tmp/hyperfine.deb", "wb").write(data)
PY

WORKDIR /app
COPY run.sh /app/run.sh
COPY src /app/src
CMD ["/app/run.sh"]
