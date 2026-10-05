#!/usr/bin/env python3
"""Self-serve lease request backend, shared shape for LightSpeed and Iron-Cold.

No auth beyond a team name (the event already started, so pre-issuing
per-team claim codes isn't practical) — abuse resistance is purely
rate-limiting + idempotency:

  - A repeat request for the SAME team name returns that team's existing
    lease instead of creating a new one (lsctl/icctl's own `lease` would
    refuse a bare repeat anyway; we check first so this is a normal,
    expected path, not an error case).
  - A given source IP may claim at most MAX_NEW_TEAMS_PER_IP *distinct*
    team names within WINDOW_SECONDS. This is the real abuse control:
    it caps how fast one visitor can drain the pool under made-up names.
    It does NOT limit how often the same team re-fetches their own
    existing lease (that's just cmd_show, free).
  - A flat per-IP request rate limit on top, to blunt simple hammering.

Listens on 127.0.0.1 only -- nginx terminates TLS and reverse-proxies
/api/lease to this. Never exposed directly.

Env:
    LEASED_CTL          path to lsctl or icctl (required)
    LEASED_MODE         "lightspeed" or "ironcold" (required) -- controls
                         output parsing and whether a token is returned
    LEASED_PORT         default 8088
    LEASED_SUDO         "1" to prefix ctl calls with sudo (lsctl/icctl
                         both need root for docker compose), default "1"
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CTL = os.environ.get("LEASED_CTL")
MODE = os.environ.get("LEASED_MODE")
PORT = int(os.environ.get("LEASED_PORT", "8088"))
USE_SUDO = os.environ.get("LEASED_SUDO", "1") == "1"

if not CTL or MODE not in ("lightspeed", "ironcold"):
    sys.exit("set LEASED_CTL and LEASED_MODE=lightspeed|ironcold")

MAX_NEW_TEAMS_PER_IP = 5
WINDOW_SECONDS = 3600
MAX_REQUESTS_PER_IP_PER_MIN = 12
TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,47}$")

_lock = threading.Lock()
_new_team_log: dict[str, list[float]] = {}   # ip -> [timestamps of distinct-team claims]
_req_log: dict[str, list[float]] = {}        # ip -> [timestamps of any request]
_known_teams_per_ip: dict[str, set] = {}      # ip -> team names already seen from this ip


def _prune(lst: list[float], horizon: float) -> list[float]:
    cutoff = time.time() - horizon
    return [t for t in lst if t > cutoff]


def rate_limit_check(ip: str, team: str) -> str | None:
    """Returns an error string if this request should be rejected, else None."""
    with _lock:
        reqs = _prune(_req_log.get(ip, []), 60)
        if len(reqs) >= MAX_REQUESTS_PER_IP_PER_MIN:
            return "too many requests, slow down"
        reqs.append(time.time())
        _req_log[ip] = reqs

        seen = _known_teams_per_ip.setdefault(ip, set())
        if team not in seen:
            claims = _prune(_new_team_log.get(ip, []), WINDOW_SECONDS)
            if len(claims) >= MAX_NEW_TEAMS_PER_IP:
                return f"too many different team names from this address recently (max {MAX_NEW_TEAMS_PER_IP}/hour) -- if this is your team and you already have a lease, re-submit the exact same team name"
            claims.append(time.time())
            _new_team_log[ip] = claims
            seen.add(team)
    return None


def run_ctl(*args: str) -> subprocess.CompletedProcess:
    cmd = (["sudo"] if USE_SUDO else []) + [CTL] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def parse_lightspeed(text: str) -> dict:
    addr = re.search(r"gRPC\s*:\s*(\S+)", text)
    tok = re.search(r"Token\s*:\s*(\S+)", text)
    if not addr:
        return {}
    out = {"address": addr.group(1)}
    if tok:
        out["token"] = tok.group(1)
    return out


def parse_ironcold(text: str) -> dict:
    addr = re.search(r"Target\s*:\s*(\S+)", text)
    return {"address": addr.group(1)} if addr else {}


PARSERS = {"lightspeed": parse_lightspeed, "ironcold": parse_ironcold}


def get_or_create_lease(team: str) -> tuple[int, dict]:
    show = run_ctl("show", team)
    if show.returncode == 0:
        info = PARSERS[MODE](show.stdout)
        if info:
            info["status"] = "existing"
            return 200, info

    lease = run_ctl("lease", team)
    out = lease.stdout + lease.stderr
    if lease.returncode != 0:
        if "already had a lease" in out:
            return 409, {"error": "this team already used its lease for this event "
                                   "(it may have ended or been released) -- contact an "
                                   "organizer if you need a new one"}
        if "mid-operation" in out:
            return 503, {"error": "your previous request for this team is still being "
                                   "created -- wait a few seconds and submit again"}
        if "pool" in out.lower() and ("full" in out.lower() or "no free" in out.lower()):
            return 503, {"error": "the lease pool is full right now -- try again in a few minutes"}
        return 500, {"error": "could not create a lease; an organizer has been notified", "detail": out[-500:]}

    info = PARSERS[MODE](out)
    if not info:
        return 500, {"error": "lease created but could not parse the response; contact an organizer", "detail": out[-500:]}
    info["status"] = "created"
    return 200, info


class Handler(BaseHTTPRequestHandler):
    server_version = "leased/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/api/lease":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                self._send_json(400, {"error": "bad request"})
                return
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send_json(400, {"error": "malformed JSON"})
            return

        team = str(body.get("team", "")).strip()
        if not TEAM_NAME_RE.match(team):
            self._send_json(400, {"error": "team name must be 1-48 characters: letters, digits, spaces, _ . -"})
            return

        # nginx sets X-Real-IP / X-Forwarded-For; fall back to the raw peer.
        ip = self.headers.get("X-Real-IP") or self.client_address[0]

        err = rate_limit_check(ip, team)
        if err:
            self._send_json(429, {"error": err})
            return

        status, info = get_or_create_lease(team)
        self._send_json(status, info)

    def do_GET(self):
        if self.path == "/api/health":
            self._send_json(200, {"status": "ok", "mode": MODE})
            return
        self._send_json(404, {"error": "not found"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"leased ({MODE}) listening on 127.0.0.1:{PORT}, ctl={CTL}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
