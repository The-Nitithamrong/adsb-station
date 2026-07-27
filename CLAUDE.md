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
  Also writes `/run/flight-watcher/inbound.json` (soonest THA inbound) for the Pixoo THA page,
  and on prune writes a per-flight row to the `tracks` table (closest approach + alt there, etc.).
- `flightwatch/track_stats.py` — reads `tracks`: coverage floor (how low we still receive near VTBS),
  actual STAR-gate→signal-loss time per gate, and actual vs computed ETA. `python3 track_stats.py [THA]`.
  STAR entry gates (all FL180, coords from the RNAV chart) are in `STAR_FIXES`:
  WILLA/NORTA/EASTE/TUMGA/LEBIM; a flight passing within `STAR_FIX_RADIUS_NM` is tagged with its gate.
- `flightwatch/adsb_view.py` — live aircraft table (debug/inspect).
- `flightwatch/outbox.py` (+ `systemd/adsb-outbox.{service,timer}`) — OPTIONAL forwarder: sends new
  `events`+`tracks` rows to a cloud sink (Cloudflare D1 now) every ~10 min. Adds a `sent` column to
  each table (idempotent ALTER — does NOT touch flight_watcher), marks rows sent; unsent rows queue
  and retry (survives connectivity gaps). D1 side: `uid` PK + `INSERT OR IGNORE` = idempotent on
  re-send. Pluggable — add a sink `send(table,cols,rows)->count` to `SINKS` (e.g. Dataverse later).
  D1 creds (`D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN`, `STATION_ID`) live in /etc/fr24-watchdog.env.
- `pixoo/{renderer,pages,main}.py` — Pixoo renderer (pixel fonts + `fontmode="1"` = no anti-alias),
  page registry, push loop. Needs PixelOperator*.ttf in pixoo/. Pages: `feeder_status`, `uptime`,
  `next_flight` (tha_inbound / flights_list kept but out of rotation). Frame rotated 180° (upside-down mount).
- `agenda/agenda_fetch.py` (+ `systemd/adsb-agenda.{service,timer}`) — OPTIONAL: fetches the next Google
  Calendar event (next flight) via the calendar's **private iCal (ICS) secret URL** over HTTPS (stdlib
  urllib — NO OAuth, runs anywhere), parses the soonest upcoming VEVENT (skips RRULE/past), extracts
  flight `code` + `route` from the summary, writes `/run/agenda/next.json` for the Pixoo `next_flight`
  page. Runs via systemd timer every ~15 min. ICS URL (`GCAL_ICS_URL`) lives in /etc/fr24-watchdog.env.
  Why ICS not the Calendar API: Pi reads local files only + ICS needs no OAuth/token refresh (stdlib-only).
- `systemd/*` — unit files for each service.
- `ha/mqtt_publish.py` (+ `systemd/adsb-ha-mqtt.{service,timer}`, `deploy/homeassistant/`) — OPTIONAL:
  publishes /run status+inbound to MQTT with Home Assistant discovery (retained config + state) every
  1 min via `mosquitto_pub` (apt mosquitto-clients; stdlib only). HA + Mosquitto run as Docker
  containers (`deploy/homeassistant/docker-compose.yml`). MQTT creds (`MQTT_HOST/PORT/USER/PASS`) in
  /etc/fr24-watchdog.env. Sensors: feeder health/rate/aircraft, received count, THA flight/ETA/dist.
- `deploy/adsb-autoupdate.sh` (+ `systemd/adsb-autoupdate.{service,timer}`) — OPTIONAL: Pi auto-pulls
  `origin/main` every ~10 min (`merge --ff-only`, skips on local conflicts), then syncs
  `/usr/local/bin` + unit files and restarts only the changed services. Runs from `/usr/local/bin`
  (self-updates). Install once (see README); once on, merged changes deploy without a manual `git pull`.

## Runtime data contract (JSON in /run, world-readable — NOT secret)
- `/run/fr24-watchdog/status.json` — written by watchdog each run (root; chmod 644 so pixoo/arin reads):
  `{ts, health: ok|recovering|dead, msg_per_s, aircraft}`. Pixoo derives `stale` from `ts` age.
- `/run/flight-watcher/inbound.json` — written by flight_watcher (arin; via `RuntimeDirectory=flight-watcher`):
  `{ts, flight, eta_min, dist_nm, alt, gs, hex}` or `{ts, flight: null}` when no THA inbound.
- `/run/agenda/next.json` — written by agenda_fetch (arin; via `RuntimeDirectory=agenda` +
  `RuntimeDirectoryPreserve=yes` so the oneshot's dir survives between runs):
  `{ts, summary, code, route, start_ts, start_str, in_min, all_day}` or `{ts, summary: null}` when no
  upcoming event. Pixoo recomputes `in_min` live from `start_ts` each tick (fetched value can be stale).

## Conventions / guardrails
- SECRETS live ONLY in /etc/fr24-watchdog.env (TG_API, TG_CHAT, HC_URL, D1_*, MQTT_*, GCAL_ICS_URL). NEVER commit .env or *.db.
  (GCAL_ICS_URL is a private calendar link — treat as secret; anyone with it reads your calendar.)
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
   ETA is now ALTITUDE-based: `alt / ETA_DESCENT_FPM` (750 ft/min avg descent), NOT straight-line
   dist/gs (unreliable — STAR arrivals don't fly straight in and gs drops during descent).
   Anchor from real ops + EASTE 1C RNAV chart: crossing the STAR at ~16000–18000 ft → ~20–25 min to
   touchdown (16000/750≈21m, 18000/750≈24m). `tracks` + `track_stats.py` collect ground truth to
   re-tune ETA_DESCENT_FPM. Signal isn't continuous to the ground — `alt_at_min` = lowest we still receive.
3. Add LINE OA notify alongside Telegram.
4. ✅ Outbox forwarder (survive connectivity gaps) — `outbox.py` → Cloudflare D1 (pluggable sink;
   chose D1 over Dataverse: source is SQLite so 1:1 + bearer-token auth vs OAuth/app-registration).
   Add a Dataverse sink to `SINKS` later if the data must drive Power Platform apps/flows.
5. ✅ DONE — watchdog writes status.json; feeder page reads it; THA-inbound page added
   (flight_watcher writes inbound.json). Remaining polish: tune THA page thresholds vs real arrivals.
6. Off-grid OPC station: solar+battery, 4G router, self power-monitoring, hardware watchdog,
   safe low-battery shutdown.
