# CLAUDE.md — adsb-station

Context for Claude Code working on this repo. Read this before changing anything.

## What this is
Raspberry Pi 5 ADS-B ground station (Bangkok, Khlong Sam Wa). Three jobs:
1. **Feeder + reliability** — feeds Flightradar24 (station `T-VTBD178`) via `fr24feed` +
   `dump1090-mutability`; a watchdog auto-recovers the dongle when it hangs.
2. **Flight watcher** — detects Thai Airways (THA) flights inbound to VTBS (Suvarnabhumi),
   alerts ~30 min before landing → feeds a crew-transport (shuttle) workflow.
3. **Status display** — Pixoo 64 shows clock + feeder health.

## Hardware / environment (this is the REAL setup — don't assume otherwise)
- Pi 5, user `arin`, hostname `Arin`. Boots from SD (`/dev/mmcblk0p2`), NOT USB.
- RTL-SDR (RTL2832U) is the ONLY USB device, at uhubctl `-l 3 -p 2`.
- Decoder `dump1090-mutability` (systemd/LSB). Feeder `fr24feed` (systemd). Bundled dump1090.
- Data ports (localhost): 30003 SBS text, 30005 Beast, 8754 fr24 status.
- Pixoo 64 IP 192.168.41.143. Station IP 192.168.41.241. journald is persistent.

## Proven / tested (source of truth — do NOT rewrite this logic without a reason)
- Watchdog v2.1 full drill PASSED: 0 msgs → L1 restart → L2 `uhubctl` cycle → dongle
  re-enumerates → 7662 msgs → Telegram OK.
- Health = REAL data-flow (count `^MSG` on 30003), NOT `fr24feed-status` (it stays "up"
  while the dongle is hung — that was the original 21-hour silent-failure bug).
- `uhubctl -l 3 -p 2 -a cycle` cuts VBUS + re-enumerates the dongle; SSH/network survive
  (Pi 5 network is not on USB). Pi 5 USB ports are ganged.
- Telegram works (creds in /etc/fr24-watchdog.env, proven send).
- Coverage from home: aircraft received at 110–131 nm → ~25–30 min lead time is real.
- THA callsigns confirmed in 30003. Prefix "THA" = Thai Airways (AIQ/NOK/BKP = other carriers).
- GOTCHA: `nc | awk` loses data to buffering — read the socket line-by-line in code
  (adsb_view.py / flight_watcher.py already do this; don't "simplify" back to pipes).

## Files
- `watchdog/fr24-watchdog.sh` — health check + escalation (L1 restart → L2 uhubctl → L3 alert)
  + healthchecks.io heartbeat. Runs via systemd timer every 5 min.
- `flightwatch/flight_watcher.py` — 30003 → THA inbound VTBS → ETA≤30m → dedupe → Telegram + SQLite.
  Also writes `/run/flight-watcher/inbound.json` (soonest THA inbound) for the Pixoo THA page.
- `flightwatch/adsb_view.py` — live aircraft table (debug/inspect).
- `pixoo/{renderer,pages,main}.py` — Pixoo renderer (pixel fonts + `fontmode="1"` = no anti-alias),
  page registry, push loop. Needs PixelOperator*.ttf in pixoo/. Pages: `feeder_status`, `tha_inbound`.
- `systemd/*` — unit files for each service.

## Runtime data contract (JSON in /run, world-readable — NOT secret)
- `/run/fr24-watchdog/status.json` — written by watchdog each run (root; chmod 644 so pixoo/arin reads):
  `{ts, health: ok|recovering|dead, msg_per_s, aircraft}`. Pixoo derives `stale` from `ts` age.
- `/run/flight-watcher/inbound.json` — written by flight_watcher (arin; via `RuntimeDirectory=flight-watcher`):
  `{ts, flight, eta_min, dist_nm, alt, gs, hex}` or `{ts, flight: null}` when no THA inbound.

## Conventions / guardrails
- SECRETS live ONLY in /etc/fr24-watchdog.env (TG_API, TG_CHAT, HC_URL). NEVER commit .env or *.db.
- Deploy model: the Pi runs `git pull`. Do not hand-edit files on the Pi.
  - GOTCHA: `fr24-watchdog.sh` runs from `/usr/local/bin/` and unit files from `/etc/systemd/system/` —
    `git pull` does NOT update those. After changing them, `cp` into place + restart / `daemon-reload`.
    (`flight_watcher.py` and `pixoo/*.py` run from the repo, so pull is enough for those.)
  - systemd directives take NO trailing `#` comment (whole line after `=` is the value) — comment on its own line.
- Edge scripts: prefer Python stdlib only (no pip deps) so they run anywhere.
- Future OPC/off-grid station: LTE modem must NOT be on USB (uhubctl L2 would power-cycle it) —
  use a 4G router over Ethernet, or a UART/HAT. Manage rooftop enclosure heat.

## Roadmap (prioritized)
1. Validate THA detection from home, then run flight_watcher as the provided systemd service.
2. Tune inbound filter + ETA vs real THA arrivals; set DEST_LAT/LON to exact OPC coords when known.
3. Add LINE OA notify alongside Telegram.
4. Add SQLite → Dataverse outbox forwarder (survive connectivity gaps).
5. ✅ DONE — watchdog writes status.json; feeder page reads it; THA-inbound page added
   (flight_watcher writes inbound.json). Remaining polish: tune THA page thresholds vs real arrivals.
6. Off-grid OPC station: solar+battery, 4G router, self power-monitoring, hardware watchdog,
   safe low-battery shutdown.
