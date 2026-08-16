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
- `flightwatch/flight_watcher.py` — 30003 → THA inbound VTBS → ETA≤30m → dedupe → SQLite `events`.
  (Per-flight Telegram REMOVED — was noise; replaced by the daily digest below. `notify()` kept but unused.)
  Also writes `/run/flight-watcher/inbound.json` (soonest THA inbound) for the Pixoo THA page,
  and on prune writes a per-flight row to the `tracks` table (closest approach + alt there, etc.).
  Also fills the `sightings` catalog: 1 row per `UNIQUE(day, flight, hex)` — day = UTC day, `flight` =
  callsign, `hex` = ICAO 24-bit, `first_seen_ts/_utc` = when that aircraft's FIRST message arrived (not
  when the callsign showed up — callsign rides MSG type 1, seconds later), plus `lat/lon/alt_ft/gs_kt`
  = the state AT first sighting (not latest — this is what makes it a coverage record). Correctness
  rests on the UNIQUE index, not on memory: state is dropped after `CLEAR_SEC`, so an aircraft that
  leaves and returns re-fires the INSERT — `INSERT OR IGNORE` keeps the earliest row.
  `p["logged_cs"]` holds the callsign already written, NOT a bool: a weak signal decodes partial
  callsigns (observed a bare `"N"`), and the old bool latched that wrong name in forever. A callsign
  shorter than `MIN_CALLSIGN_LEN` is ignored outright.
  A callsign CHANGE is then treated by WHERE it happens, and the distinction came from station data:
  - across contacts (state was dropped after `CLEAR_SEC`, so `logged_cs` is None) = the airframe landed
    and flew out again — a real Bangkok transit. Written immediately; that second row is the point.
  - inside ONE contact = physically impossible (aircraft don't rename mid-air). Observed as pairs of
    hex differing by one bit (781BB5/781FB5, 8851E5/8851E7, often `reg` NULL) reporting each other's
    callsign at the SAME second from contradictory positions — an ADS-B bit error leaking another
    aircraft's message into this hex. Needs `MIN_CALLSIGN_HITS` repeats before it is believed, since a
    corrupted callsign rarely repeats identically.
  `record_sighting` takes the timestamp explicitly because it means different things per case: for the
  first callsign it is `seen_ts` (first contact with the airframe = the coverage figure), for a later
  one it is now. Passing `seen_ts` for both is what made the old bad rows carry one timestamp with two
  different positions.
  Two fields arrive late and are filled by a single UPDATE each, tracked by `pos_state`
  (0 pending → 1 filled → 2 aircraft left without ever sending position):
  position (`fill_sighting_pos`, since callsign usually precedes position) and `reg`
  (`reg_lookup.py` — ADS-B carries no registration, only hex; never look it up inline, `parse()` is the
  socket hot loop). outbox ships only rows where BOTH are settled, because its D1 sink is
  INSERT OR IGNORE and could never correct a row sent early.
  GOTCHA (`is_inbound`): "closing" is judged from `dist_hist` spaced by TIME (`HIST_MIN_SEC`=15s), NOT
  by fix count — dump1090 sends many msg/s AND `lat/lon` persist in state so `dist_hist` was appended on
  EVERY message (~10/s), making the last-6 window span <1s → distance barely changes → `closing` (needs
  >1nm) never true → is_inbound False for real arrivals (obs: 8 THA landed, `events`=0). Time-spacing
  fixes it. Also reads `vrate` (SBS field 16, type-4 velocity): climb >`VRATE_CLIMB_FPM` = departure →
  excluded even if closing+low (kills the departing-TG false positive); descend < -`VRATE_DESC_FPM` = a
  direct inbound signal (falls back to alt-trend when vrate not yet received).
- `flightwatch/track_stats.py` — reads `tracks`: coverage floor (how low we still receive near VTBS),
  actual STAR-gate→signal-loss time per gate, arrivals-by-hour-of-day (BKK traffic distribution), and
  actual vs computed ETA. `python3 track_stats.py [THA]`.
  STAR entry gates (all FL180, coords from the RNAV chart) are in `STAR_FIXES`:
  WILLA/NORTA/EASTE/TUMGA/LEBIM; a flight passing within `STAR_FIX_RADIUS_NM` is tagged with its gate.
- `flightwatch/adsb_view.py` — live aircraft table (debug/inspect).
- `report/daily_status.py` (+ `systemd/adsb-daily-report.{service,timer}`) — sends a once-a-day Telegram
  digest at 09:00 Asia/Bangkok (timer `OnCalendar=... Asia/Bangkok`): feeder health/rate/aircraft (from
  status.json) + Pi uptime/temp/throttle(undervoltage)/load/disk/RAM + today's fan on-count/on-time.
  stdlib (urllib), reuses TG_API/TG_CHAT. Heartbeat — the watchdog L3 station-down alert still fires too.
- `report/fan_stats.py` — per-day fan on-count + total on-time from `/home/arin/fan_events.jsonl`
  (append-only `{ts,on}` log written by mqtt_publish on every switch on↔off transition, ~1-min res).
  `python3 fan_stats.py [days]`; `today_stats()` is imported by daily_status for the digest line.
- `report/heartbeat.py` (+ `systemd/adsb-heartbeat.{service,timer}`) — "black box" recorder for the
  silent-hang problem. Every ~10 min pushes ONE health snapshot to Cloudflare D1 table `heartbeat`
  (reuses outbox's D1 pattern; `uid`=station:ts + `INSERT OR IGNORE` = idempotent) AND appends to local
  `/home/arin/heartbeat.jsonl` backup. Snapshot = `throttled` (RAW hex — keeps sticky "occurred" bits
  0x1_0000+ so a brief undervoltage between ticks is caught), `volts_core`, `temp_c`, `freq_arm_mhz`,
  `load1/5/15`, `mem_avail_mb`, `disk_used_pct`, `uptime_s`, feeder `msg_per_s/aircraft/health`.
  WHY: journald keeps getting wiped by the freeze (green-LED-stuck kernel hang) — cloud survives it, so
  the LAST row before the silence gap = the pre-hang state. Reboot shows as `uptime_s` dropping (that
  also separates a real hang from a mere network drop — a net drop leaves `uptime_s` climbing). Hang
  reproduced on BOTH SD and NVMe → storage/USB ruled out; remaining suspects power/kernel/thermal, which
  these fields pin down. CREATE the D1 table once before use (`HEARTBEAT_SCHEMA` at file end / README).
  D1 creds + `STATION_ID` reuse the same /etc/fr24-watchdog.env keys as outbox.
- `flightwatch/reg_lookup.py` (+ `systemd/adsb-reg-lookup.{service,timer}`) — fills `sightings.reg`.
  ADS-B has NO registration field — only the ICAO 24-bit `hex`, so `HS-TKF` must be looked up from
  `8801f2`. Separate timer (~10 min) because the lookup is HTTP and `flight_watcher.parse()` must never
  block on the network. `reg_state` drives the handoff: 0 pending → 1 resolved → 2 gave up after
  `REG_MAX_TRIES` (unknown/military hex); outbox only ships rows with `reg_state != 0`, so a consumer
  never sees a half-filled row and `registration: null` always means "not in the registry", never "not
  looked up yet". Permanent local `aircraft(hex→reg)` cache = one upstream call per airframe EVER
  (daily repeat visitors hit cache). Source is `REGDB_URL` (default hexdb.io); `pick()`/`REG_KEYS`
  accept several JSON field spellings so swapping to adsbdb needs no code change. A network error
  ABORTS the run instead of counting a try — else a flat upstream would mark good aircraft unknown.
- `flightwatch/outbox.py` (+ `systemd/adsb-outbox.{service,timer}`) — OPTIONAL forwarder: sends new
  `events`+`tracks`+`sightings` rows to a cloud sink (Cloudflare D1 now) every ~10 min. Adds a `sent`
  column to each table (idempotent ALTER — does NOT touch flight_watcher), marks rows sent; unsent rows
  queue and retry (survives connectivity gaps). D1 side: `uid` PK + `INSERT OR IGNORE` = idempotent on
  re-send. Pluggable — add a sink `send(table,cols,rows)->count` to `SINKS` (e.g. Dataverse later).
  Per-table `where` = extra "ready to send" gate (`sightings` uses `reg_state != 0 AND pos_state != 0`)
  — needed BECAUSE the sink is INSERT OR IGNORE: a row sent while `reg`/position are still NULL could
  never be corrected later. GOTCHA: `main()`'s per-table loop var must NOT be named `cfg` — that
  shadows the module-level `cfg()` env helper and makes it local to the whole function
  (`UnboundLocalError` on the first call). It is `tcfg`.
  D1 creds (`D1_ACCOUNT_ID/D1_DATABASE_ID/D1_API_TOKEN`, `STATION_ID`) live in /etc/fr24-watchdog.env.
- `api/sightings-worker/` — READ-ONLY Cloudflare Worker serving the `sightings` catalog over HTTP.
  `/flights` (1 row per flight — the "what flew that day" question), `/aircraft` (1 row per airframe +
  its callsigns; `?transit=1` = flew more than one flight), `/days` (per-day counts for a date picker),
  `/sightings` (raw rows with position), `/health`.
  DAY HANDLING IS THE CORE OF THIS API: the `day` column is a UTC day, so matching it directly answers
  the wrong question for a Thai user — "the 15th" would return 07:00 on the 15th → 07:00 on the 16th
  BKK, losing 00:00–07:00. Every endpoint converts `date`+`tz` (default `+07:00`) into an epoch window
  and filters on the indexed `first_seen_ts`, and ALWAYS echoes `window_utc` so the caller can see
  which window they actually got. `airline=THA` is a callsign prefix filter.
  `/aircraft.span_min` is what separates a real transit from noise: a genuine turnaround spans tens of
  minutes, while span ≈ 0 means one airframe reported two callsigns in the same second — impossible,
  so it is an ADS-B bit error leaking another aircraft's message into this hex (seen as hex pairs
  differing by one bit, e.g. 781BB5/781FB5, often with `reg` NULL). Deployed at
  `adsb-sightings-api.happytohelp.workers.dev`; `API_KEY` secret is SET so a bearer is required. All caller values are bound params (no SQL string building); CORS `*`;
  optional bearer via a `API_KEY` secret (unset = public read). `has_more` = "got exactly `limit` rows"
  rather than a second `COUNT(*)` (D1 bills rows read). Writes stay with outbox's D1 REST token, which
  never leaves the Pi. `schema.sql` must be applied to D1 ONCE before the Pi starts pushing (see its
  README) — until then outbox logs a per-run `sightings` failure and queues the rows; nothing is lost.
- `report/inbound_push.py` (+ `systemd/adsb-inbound-push.service`) — OPTIONAL: pushes ALL live traffic
  (every airline, not just THA) to Cloudflare D1 table `inbound_live` every ~30s so an EXTERNAL web app
  can show real-time arrivals AND departures. Source = `inbound_all` + `outbound_all` in
  `/run/flight-watcher/inbound.json`, tagged with a `direction` column (`inbound`/`outbound`);
  `eta_min` is NULL for outbound (a departing aircraft has no meaningful arrival ETA). Differs from
  outbox (history: append) — this is LIVE-only, MANY rows (1/aircraft, PK `station+hex`): each cycle
  `INSERT OR REPLACE` the current set (push_ts=now) then `DELETE ... push_ts < now` (drops aircraft
  that left, no empty gap). DELETE runs only after every INSERT chunk succeeds — a half-written set
  would show as a table with holes.
  GOTCHA: D1 caps bound params at ~100/query, so rows MUST be chunked (`D1_MAX_PARAMS`, same as
  outbox). Sending one INSERT for every aircraft worked at ~12 aircraft and then returned HTTP 400
  `too many SQL variables` for every push once traffic hit 22 (12 cols × 22 = 264 params) — the table
  sat empty for days while the log only said "ส่ง D1 ไม่สำเร็จ (เน็ตหลุด?)".
  Daemon (Type=simple, reuses the same `D1_*` creds + push pattern as heartbeat/outbox; stdlib).
  Create `inbound_live` once (`INBOUND_SCHEMA` at file end; ALTER lines there for older tables —
  `ensure_table()` uses CREATE IF NOT EXISTS so it will NOT add columns to an existing table).
  Web app reads via a Pages D1 binding —
  `SELECT * FROM inbound_live WHERE station=? AND direction='inbound' ORDER BY eta_min`.
  COST (measured, not estimated — from D1 GraphQL analytics `d1AnalyticsAdaptiveGroups`): this table
  rewrites its whole row set every cycle, so writes = rows × cycles/day × (1 + indexes). At 36 aircraft
  on a 30s cycle that was ~9,700 rows-written/HOUR ≈ 233k/day — 2.3× over D1's free-tier 100k/day, and
  the cap is per-DATABASE, so blowing it takes outbox/heartbeat/sightings down too, not just this table.
  Two fixes applied: `PUSH_INTERVAL_S` default now 120 (30 → 60 → 120, re-measured each time), and
  DROP the secondary index. D1 counts index writes as rows written, so an index on a table this small
  costs a full extra write per row per cycle while saving nothing (a few dozen rows scan instantly, and
  rows-READ is identical either way). 60s measured ≈ 72k/day which was still too tight: with sightings
  and tracks the total sat at ~82k of 100k, leaving no room for a backlog — and one 19k-row outbox
  backlog costs ~39k writes on its own. 120s ≈ 36k/day, total ~46k, half the quota free.
  Any new index anywhere is a standing daily cost — `sightings.day`'s index was dropped for the same
  reason once the API moved to `first_seen_ts` windows and stopped filtering on `day` at all.
- `report/eta_push.py` (+ `systemd/adsb-eta-push.service`) — OPTIONAL: HTTP POST THA inbound ETA to the
  busandgo geofence/shuttle Cloudflare Worker (`/flights/eta`) every ~30s → feeds the crew-transport
  workflow (knows when a TG flight lands → geofence trigger). Differs from inbound_push (D1, ALL
  airlines): this is HTTP-direct, THA-only. Source = `inbound_all` in inbound.json, filtered to callsign
  `THA*`; `flight_number` = `THA476`→`TG476` (ICAO→IATA prefix swap); `eta` = BKK clock `"HH:MM"`
  (now+eta_min). ADJUST-from-stats: adds `bias = median(actual−computed)` over `tracks` (THA, watched=1)
  — the same actual-vs-computed ETA metric as `track_stats.py` §4 (`actual=(last_ts−alert_ts)/60 +
  last_alt/900`) — so the pushed ETA self-calibrates toward real touchdown time as tracks accumulate
  (needs ≥20 tracks, else bias=0). Body
  `{source:"pi-radar", eta_factor, eta_factor_samples, updates:[{flight_number,eta, +position}]}` —
  `eta_factor`/`eta_factor_samples` are body-level (one value per push) so the consumer knows how much
  the ETA was scaled and off how many tracks; `samples < FACTOR_MIN_SAMPLES` (20) means NOT calibrated
  yet and `eta_factor` is forced to 1.0 (factor alone can't distinguish that from a true 1.0, so
  `eta_factor()` returns `(factor, n)`). Position =
  `lat,lon,altitude_ft,ground_speed_kt,track_deg,distance_nm` (raw values at that
  moment — the ETA factor corrects ETA only; `distance_nm` is to VTBS, not to the station). Every
  position field is OPTIONAL and is OMITTED when not yet received (never sent as `null` — the worker
  upserts by flight_number, so a null would overwrite a good stored value). Mapping lives in
  `POSITION_FIELDS`; add a field there + in `all_inbound()` to extend. Bearer
  `ETA_INGEST_KEY`; worker upserts by flight_number (should age-out — no landed events sent). Daemon
  (Type=simple, stdlib). Creds `ETA_INGEST_KEY`(+`ETA_INGEST_URL` opt) in /etc/fr24-watchdog.env.
  GOTCHA: must send a browser-ish `User-Agent` — Cloudflare Bot-Fight/Browser-Integrity blocks the default
  `Python-urllib` UA at the edge with HTTP 403 "error code: 1010" BEFORE it reaches the Worker (not an auth
  fail; a real token still 403s). `USER_AGENT="Mozilla/5.0 (pi-radar; ...)"` fixed it. Worker only updates
  flights it already knows (roster) — an unknown flight_number returns `applied:0, reason:"no such flight"`
  (that's normal for a test id like TG999, not an error). `ETA_INGEST_KEY` must be ASCII (a non-ASCII paste
  would break the HTTP header → daemon guards against it and skips rather than crash-looping).
- `pixoo/{renderer,pages,main}.py` — Pixoo renderer (pixel fonts + `fontmode="1"` = no anti-alias),
  page registry, push loop. Needs PixelOperator*.ttf in pixoo/. Pages: `feeder_status`, `uptime`,
  `next_flight` (tha_inbound / flights_list kept but out of rotation). Frame rotation via `ROTATE` in main
  (0 = normal mount; 180 = upside-down mount — flip if the display reads inverted).
  ANIMATION: main loop reads /run data every `REFRESH`s but pushes `ANIM_FPS` frames/s (default 2) and
  sets `data["anim"]=phase` per frame. Moving bits: `draw_scanner()` comet around the frame border (every
  page), the clock colon blinks 1 Hz (`draw_header` swaps ':'→' ', same glyph width so digits don't shift),
  and the UP-page fan spins when on (`draw_fan(...,frame=)` alternates 2 blade frames). Push wrapped in
  try/except (Pixoo WiFi drops crash-looped before). Tune SCAN_SPEED/SCAN_TAIL in renderer, ANIM_FPS in main.
  PUSH HANG (fixed): the `pixoo` lib calls `requests` with NO timeout — a WiFi/router blip mid-push left the
  socket half-open and the loop BLOCKED FOREVER (display froze, `pixoo.service` still "active" but ~0 CPU, no
  log, no exception → the try/except never fired). Fix: main monkeypatches `requests.Session.request` to
  inject `timeout=PUSH_TIMEOUT` (+ `socket.setdefaulttimeout`) so a hung push RAISES → caught → after
  `PUSH_FAIL_RECONNECT` consecutive fails it re-creates `Pixoo()` (reconnect + reset frame counter). Now the
  display self-heals after any network blip instead of needing a manual `systemctl restart pixoo`.
  COFFEE BREAK (`COFFEE_ENABLE`, currently OFF — set True to re-enable; also toggled at RUNTIME by the ESP32
  COFFEE button → `uptime_server` writes `/home/arin/pixoo_coffee` "1"/"0", `coffee_enabled()` reads it each
  loop, file-absent falls back to `COFFEE_ENABLE`): every `COFFEE_EVERY_MIN` (60) in
  [`COFFEE_START_H`:00..`COFFEE_END_H`:00] (08:00–20:00,
  machine=BKK) main overrides the rotation with the `coffee_break` page for `COFFEE_SHOW_SEC` and fires the
  Pixoo buzzer via `COFFEE_BUZZ` (`Device/PlayBuzzer`) — one fire-and-forget POST; the device loops
  `ActiveTimeInCycle`/`OffTimeInCycle` for `PlayTotalTime` (500/500/5000 = ~5 spaced beeps in 5s).
  GOTCHA: the param names are `ActiveTimeInCycle`/`OffTimeInCycle`, NOT `PlayPulseTime`/`PlayOffTime` —
  wrong names = the device stays silent. Window checked in minutes-of-day so
  20:30 doesn't fire. Slot = `%Y%m%d-<minsOfDay//EVERY>` → beeps once per slot; init to the startup slot so
  a restart mid-slot doesn't beep.
  KNOCK-OFF: at `KNOCKOFF_H`:00 (22:00 BKK) main shows the `knock_off` page (crescent moon + twinkling
  stars, "TIME TO BED") for `KNOCKOFF_SHOW_SEC` and buzzes ONCE — a daily bedtime reminder. Fires once/day
  (dedup by `%Y%m%d` when minsOfDay ≥ 22:00; `last_knockoff_day` inits to today if started after 22:00 so a
  late restart doesn't re-fire). Page priority in the loop: knock_off > coffee_break > normal rotation.
  NAP MODE: both buzzers (coffee + knock-off) are auto-silenced when the next Google Calendar event is within
  `NAP_BEFORE_H` (24) hours — `napping = 0 ≤ agenda.in_min ≤ NAP_BEFORE_H*60` (from `/run/agenda/next.json`).
  Lets the pilot nap before a flight without the hourly beep; set `NAP_BEFORE_H=0` to disable.
- `agenda/agenda_fetch.py` (+ `systemd/adsb-agenda.{service,timer}`) — OPTIONAL: fetches the next Google
  Calendar event (next flight) via the calendar's **private iCal (ICS) secret URL** over HTTPS (stdlib
  urllib — NO OAuth, runs anywhere), parses the soonest upcoming VEVENT (skips RRULE/past), extracts
  flight `code` + `route` from the summary, writes `/run/agenda/next.json` for the Pixoo `next_flight`
  page. Runs via systemd timer every ~15 min. ICS URL (`GCAL_ICS_URL`) lives in /etc/fr24-watchdog.env.
  Why ICS not the Calendar API: Pi reads local files only + ICS needs no OAuth/token refresh (stdlib-only).
- `systemd/*` — unit files for each service.
- `ha/mqtt_publish.py` (+ `systemd/adsb-ha-mqtt.{service,timer}`, `deploy/homeassistant/`) — OPTIONAL:
  publishes /run status+inbound to MQTT with Home Assistant discovery (retained config + state) every
  1 min via `mosquitto_pub` (apt mosquitto-clients; stdlib only). HA runs as a Docker container
  (`deploy/homeassistant/docker-compose.yml`) — MOVING Pi#1 → **Pi#2 (ArinII)** so it can safely
  power-cycle Pi#1 (HA on the box it cuts = can't turn itself back on). On Pi#2 it uses the **native
  fleet Mosquitto** (apt, `pi-ha/mosquitto/fleet.conf`) as the single broker for BOTH fleet topics and
  HA discovery — no Docker mosquitto (port clash). Migration runbook: `deploy/homeassistant/MIGRATE_HA_TO_PI2.md`.
  MQTT creds (`MQTT_HOST/PORT/USER/PASS`, user `adsb`) in /etc/fr24-watchdog.env — `MQTT_HOST` = Pi#2 IP
  after the move. Sensors: feeder health/rate/aircraft, **CPU temp** (unit °C, state_class
  measurement — NO device_class temperature on purpose: device_class makes HA convert °C↔°F by unit
  system, which can revert/override on HA config corruption and silently break the numeric_state fan
  automation; raw °C = stable),
  **power/throttle** (`vcgencmd get_throttled` decoded: ok / undervoltage / throttled / freq-capped /
  soft-temp / "ok (… occurred)" sticky history — needs user in group `video`, else "unknown"),
  received count, THA flight/ETA/dist. Also SUBSCRIBES (reverse direction) to `adsb/<sid>/fan` (retained,
  published by an HA automation on the Tuya cooling-fan switch) via `mosquitto_sub -C 1 -W 2` and writes
  `/run/adsb-ha/fan.json` for the Pixoo UP-page fan icon (real switch state, ~1 min lag).
  GOTCHA (HA automations): HA slugifies the discovery device name `ADS-B <host>` for entity_ids —
  `-` → `_`, `/` → `_` — so entities are `sensor.ads_b_<host>_*` NOT `adsb_<host>_*`
  (e.g. `sensor.ads_b_arin_cpu_temperature`, `sensor.ads_b_arin_power_throttle`). Wrong id →
  numeric_state conditions silently stay False (fan never toggles). Always copy the real entity_id from
  HA → Developer Tools → States; never guess. A robust fan automation also needs a `time_pattern`
  re-check (numeric_state only fires on threshold *crossings* — a missed edge leaves the fan stuck).
  FAN THRESHOLDS ARE BOUNDED BY MEASURED PHYSICS, not by taste (measured off the D1 `heartbeat` table,
  138 snapshots / 23 h at a flat `load1`≈0.4 — so temp tracked the fan, not load): fan ON = 46.3–49.6 °C,
  fan OFF = 55.1–61.1 °C, i.e. the fan is worth ~10.5 °C and settles in <10 min. BOTH thresholds must sit
  inside the 49.6–55.6 °C gap between those two states, else the automation degenerates silently:
  `above` ≥ 56 is over the natural ceiling so the fan can essentially never start (the real reason
  `above: 60` looked broken — the Pi only touched 60.6/61.1 for one sample and `numeric_state` needs a
  crossing), and `below` ≤ 49 is under the fan-on floor so it can never stop (= the "stuck on 24 h" bug
  again). Hence 55/50. "Too hot" for a Pi 5 is 80 °C (throttle) / 85 °C (hard) — this station's all-time
  max is 61.1 °C with `throttled`=0x0 on every row, so the fan buys margin, it does not prevent damage;
  ~47 °C flat is unreachable by ANY threshold pair (needs the fan on permanently — 7 W ≈ 25 THB/month).
  Re-measure both states from `heartbeat` before re-tuning if the case/location/load changes.
- `deploy/adsb-autoupdate.sh` (+ `systemd/adsb-autoupdate.{service,timer}`) — OPTIONAL: Pi auto-pulls
  `origin/main` every ~10 min (`merge --ff-only`, skips on local conflicts), then syncs
  `/usr/local/bin` + unit files and restarts changed services. On any `systemd/` change it also
  AUTO-ENABLES repo timers that are `disabled` (new timers deploy hands-free, no SSH) and restarts
  `enabled` ones; also AUTO-ENABLES new daemon services (`Type=simple`, no paired `.timer`, has
  `[Install]` — e.g. `adsb-uptime`) every cycle if `disabled`; `masked` units are skipped in the
  enable loop, but `systemctl mask` CANNOT actually be applied to these units: mask works by symlinking
  `/etc/systemd/system/<unit>` to /dev/null and autoupdate has already put a REAL file there, so it
  fails with "File ... already exists". To pause a unit, use a drop-in condition instead — `.d/` dirs
  are never touched by the sync, a failing condition makes `systemctl restart` skip the unit and exit
  0 (so autoupdate cannot revive it), and it survives reboot:
  `printf '[Unit]\nConditionPathExists=!/etc/adsb-paused-<unit>\n' > /etc/systemd/system/<unit>.d/pause.conf`
  then `touch /etc/adsb-paused-<unit>` + `daemon-reload` + `stop`. Resume = remove the flag file. Runs from
  `/usr/local/bin` (self-updates). Install once (see README); merged changes deploy without a manual pull.
- `deploy/uptime_server.py` (+ `systemd/adsb-uptime.service`) — OPTIONAL: tiny stdlib HTTP endpoint
  (`:8099`, `Type=simple` `Restart=always`) that serves the Pi's `/proc/uptime` seconds as plain text so
  the ESP32 display watchdog can show REAL Pi uptime ("Pi up 1D 4H") — it only pings TCP:22 otherwise and
  can't read uptime. Also routes `/coffee` (read), `/coffee/toggle|on|off` (write `/home/arin/pixoo_coffee`)
  so the ESP32 COFFEE button turns the Pixoo coffee-break on/off at runtime. Display-only + best-effort: NOT
  the watchdog's liveness check (that stays TCP:22; an
  app-level server can crash while the Pi is fine → false reset). It's a `.service` not a `.timer`, so
  autoupdate auto-enables it (daemon service = `Type=simple`, no paired `.timer`, has `[Install]`) within
  ~1 extra cycle — no SSH needed. to keep it off use the drop-in pause above (`systemctl mask` does not work here).
- `watchdog-esp32-display/` — ESP32 CYD2USB (ST7789 240×320 + touch) watchdog with a screen: NORMAL mode
  pings Pi TCP:22 every 30s → silent >15 min → power-cycle; BACKUP mode = on-screen RESET button (2-tap).
  Cuts power via ONE HA webhook (HA automation does turn_off→delay→turn_on; HA must be a SEPARATE box from
  the Pi it cuts). Screen shows Pi OK/DOWN, "Pi up" (from adsb-uptime endpoint), "last check HH:MM:SS".
  Display rotates 2 pages every `PAGE_SWITCH_MS` (5 min): clock ↔ Pi-status (the RESET-button page); tapping
  the clock page jumps to status immediately so RESET is always reachable; monitoring runs regardless of page.
  Status page also has a COFFEE ON/OFF button (top-right) that toggles the Pixoo coffee-break via the Pi's
  `:8099 /coffee/toggle` endpoint (polls `/coffee` each check to stay in sync).
  PlatformIO build flags (no User_Setup.h); DRY_RUN=1 tests UI without firing HA. `src/config.h` gitignored.

## Runtime data contract (JSON in /run, world-readable — NOT secret)
- `/run/fr24-watchdog/status.json` — written by watchdog each run (root; chmod 644 so pixoo/arin reads):
  `{ts, health: ok|recovering|dead, msg_per_s, aircraft}`. Pixoo derives `stale` from `ts` age.
- `/run/flight-watcher/inbound.json` — written by flight_watcher (arin; via `RuntimeDirectory=flight-watcher`):
  `{ts, flight, eta_min, dist_nm, alt, gs, hex}` or `{ts, flight: null}` when no THA inbound.
  Plus `nrx`, `list` (Pixoo) and `inbound_all` — every inbound aircraft, any airline:
  `{flight, hex, eta_min, dist_nm, alt, gs, lat, lon, trk}` (`lat/lon` 4 dp ≈ 11 m; any field may be
  `null` until that SBS message type arrives). Consumers: inbound_push → D1, eta_push → worker.
  And `outbound_all` — same shape minus `eta_min`, aircraft that just departed VTBS (`is_outbound`).
  Kept as its OWN key, not merged into `inbound_all`, because eta_push reads `inbound_all` to push
  arrival ETAs: a departure mixed in there would be pushed to busandgo as an inbound ETA.
- `/run/agenda/next.json` — written by agenda_fetch (arin; via `RuntimeDirectory=agenda` +
  `RuntimeDirectoryPreserve=yes` so the oneshot's dir survives between runs):
  `{ts, summary, code, route, start_ts, start_str, in_min, all_day}` or `{ts, summary: null}` when no
  upcoming event. Pixoo recomputes `in_min` live from `start_ts` each tick (fetched value can be stale).
- `/run/adsb-ha/fan.json` — written by mqtt_publish (arin; via `RuntimeDirectory=adsb-ha` +
  `RuntimeDirectoryPreserve=yes`): `{ts, on}` where `on` = true/false/null (null = HA fan topic not
  set up / bridge down). Source = HA automation republishing the Tuya switch state to `adsb/<sid>/fan`.
  Pixoo derives unknown from `ts` age (`FAN_STALE_SEC`) → hides the icon.

## Conventions / guardrails
- SECRETS live ONLY in /etc/fr24-watchdog.env (TG_API, TG_CHAT, HC_URL, D1_*, MQTT_*, GCAL_ICS_URL, ETA_INGEST_KEY). NEVER commit .env or *.db.
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
   ETA is now ALTITUDE-based: `alt / ETA_DESCENT_FPM` (900 ft/min avg descent), NOT straight-line
   dist/gs (unreliable — STAR arrivals don't fly straight in and gs drops during descent).
   Anchor from real ops + EASTE 1C RNAV chart: crossing the STAR at ~16000–18000 ft → ~18–20 min to
   touchdown (16000/900≈18m, 18000/900≈20m). Retuned 750→900 from 1146 THA tracks (track_stats): 750
   overestimated ETA by ~4.3 min; effective alt@gate÷time ≈ 863–969 fpm (~890). `tracks` +
   `track_stats.py` collect ground truth to re-tune. Signal isn't continuous to the ground —
   `alt_at_min` = lowest we still receive.
3. Add LINE OA notify alongside Telegram.
4. ✅ Outbox forwarder (survive connectivity gaps) — `outbox.py` → Cloudflare D1 (pluggable sink;
   chose D1 over Dataverse: source is SQLite so 1:1 + bearer-token auth vs OAuth/app-registration).
   Add a Dataverse sink to `SINKS` later if the data must drive Power Platform apps/flows.
5. ✅ DONE — watchdog writes status.json; feeder page reads it; THA-inbound page added
   (flight_watcher writes inbound.json). Remaining polish: tune THA page thresholds vs real arrivals.
6. Off-grid OPC station: solar+battery, 4G router, self power-monitoring, hardware watchdog,
   safe low-battery shutdown.
   - ✅ hardware watchdog — `deploy/enable-hw-watchdog.sh` (systemd `RuntimeWatchdogSec` → bcm watchdog).
     KEY INSIGHT: `fr24-watchdog` is a SOFTWARE watchdog on the same Pi — a full Pi hang (kernel lockup /
     SD I/O stall) kills it too, so it can't recover the dongle. A dongle that looks "dead" (warm + no
     data) may actually be the whole Pi hung, NOT the dongle — the original "21-hour silent failure" could
     have been this. Hardware watchdog resets the Pi on kernel/systemd hang; software watchdog can't.
   - Observed: spontaneous full hangs (green ACT LED stuck solid, fan spinning, only power-cycle recovers).
     One correlated ~04:00 with `cron.daily` (man-db/apt CPU+I/O spike). UPDATE: the hang reproduced on
     BOTH SD and NVMe → **storage/USB ruled out** (NVMe is PCIe, not USB); remaining suspects are
     **power (brownout under load spike) / kernel / thermal**. The `cron.daily` correlation now reads as a
     CPU-load spike (→ power draw spike) trigger, not an SD-write spike. `vcgencmd get_throttled`="ok" does
     NOT clear power — a hard brownout freezes before it can log, and sticky bits reset on power-cycle.
   - journald persistence kept getting lost across hangs (dir existed since Jun but journald ran volatile
     until a restart). Now forced `Storage=persistent` so the NEXT hang's `journalctl -b -1` is captured.
   - FR24 feed history pinned one outage start at **20:05 UTC = 03:05 BKK** — the same early-morning window.
     PRIME network-side suspect: a **scheduled Xiaomi mesh reboot / firmware auto-update ~03:00–04:00 BKK**.
     A router reboot mid-night → WiFi drops → fr24feed upload breaks + Pixoo push fails, and the Pi's
     onboard WiFi (brcmfmac) often fails to re-associate after the AP vanishes → looks like a dead Pi.
     ACTION: check the router's 定时重启 (scheduled restart) + auto-update and disable / move off-hours.
     This may be SEPARATE from the true kernel freeze (green-LED-stuck) — heartbeat's `uptime_s` + local
     jsonl distinguish them: a network-only drop keeps `uptime_s` climbing and keeps writing local rows;
     a real hang resets `uptime_s` and leaves a local-jsonl gap too. Longer-term for WiFi: wire Ethernet,
     or `iw wlan0 set power_save off`.
   - ✅ black box — `report/heartbeat.py` pushes a health snapshot to Cloudflare D1 every ~10 min so the
     pre-hang state survives the freeze (journald doesn't). Diagnose a hang by reading the last few D1 rows
     before the silence gap: temp climbing → thermal; `throttled` bit / `volts_core` dip → power; `load`
     spike → cron/CPU trigger; all-normal-then-silent → hard brownout. Reproduce without waiting: load all
     cores (`stress-ng --cpu 4`) while watching throttle/temp. Also try firmware/EEPROM update
     (`apt full-upgrade` + `rpi-eeprom-update -a`) for known Pi 5 hang fixes, and a known-good 5V/5A PSU+cable.
