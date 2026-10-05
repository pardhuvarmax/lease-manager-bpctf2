# Self-serve lease request page

Deployed, both live:
- https://ironcold.breachpoint.live/lease.html
- https://lightspeed.breachpoint.live/lease.html

Lets a team get their own instance without an organizer manually running
`icctl`/`lsctl lease "Team"` and DMing the result — replaces that manual
step for the common case. `icctl export` / `lsctl export` still exist for
bulk CSV export if needed.

## What it is

- `leased.py` — stdlib-only Python HTTP server (no Flask), listens on
  `127.0.0.1:8088`. One `systemd` service per VM (`leased.service`),
  parametrized by `LEASED_MODE` (`ironcold` or `lightspeed`) and
  `LEASED_CTL` (path to `icctl`/`lsctl`). Runs as root (needs it for
  `docker compose` via the ctl tools).
- `lease.html` — single static page, vanilla JS, posts to `/api/lease`.
  `__MODE__` in the body's `data-mode` attribute is substituted per
  deployment (`sed 's/__MODE__/ironcold/'` etc.) at deploy time.
- nginx reverse-proxies `/api/` to the backend; everything else is the
  existing static site, unchanged.

## Auth model (deliberate, not an oversight)

No pre-issued per-team secret — the event had already started when this
was built, so distributing codes ahead of time wasn't practical. Just a
team name, with:

- **Idempotency**: a repeat request for the same team name returns their
  *existing* lease (`icctl`/`lsctl show` first), never creates a new one.
  `icctl`/`lsctl lease` itself refuses a bare repeat for the same team
  regardless (one lease per team for the event), so this is the normal
  path, not a workaround.
- **Per-IP cap**: at most 5 *distinct* team names per source IP per hour
  (`MAX_NEW_TEAMS_PER_IP` / `WINDOW_SECONDS` in `leased.py`). This is the
  actual abuse control — it caps how fast one visitor can drain the pool
  under made-up names. Doesn't limit how often a team re-fetches its own
  lease.
- **Flat rate limit**: 12 requests/minute/IP on top, against simple
  hammering.

Known gap, accepted: nothing stops someone claiming a lease under a name
that isn't really their team (no identity verification at all). The
per-IP cap bounds the damage (at most 5 slots per source per hour, not
the whole pool), but doesn't prevent it outright. If this becomes a real
problem mid-event, the fix is `icctl`/`lsctl bad N` to quarantine an
abused slot, not a code change.

## Known timing

`icctl`/`lsctl lease` genuinely takes 10-25s (cold `docker compose up`
per slot) — the page's "requesting..." state can sit for a while on a
first request for a new team. This is not a bug; don't shorten any
client-side timeout to "fix" it. A request that times out client-side
has very likely still succeeded server-side — a follow-up submission of
the same team name will return `"status": "existing"` with the real
lease, not create a duplicate.

## Redeploying after a leased.py change

```bash
gcloud compute scp leased.py <vm>:~/leased.py --zone=us-central1-a --project=breachpoint-overflow-15140 --tunnel-through-iap
gcloud compute ssh <vm> --zone=us-central1-a --project=breachpoint-overflow-15140 --tunnel-through-iap --command='
  sudo cp ~/leased.py /opt/leased/leased.py && sudo systemctl restart leased'
```

## Where

Both VMs are in the **overflow project**, `breachpoint-overflow-15140`
(see `challenge-env/env.md` for why — the original project's
project-wide CPU quota couldn't fit either VM alongside everything
else already running). `us-central1-a`, same IAP-only-SSH convention as
every other VM this event.
