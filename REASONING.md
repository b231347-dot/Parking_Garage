# Implementation Reasoning

This document describes the system that is actually in the repository, not an
intended future architecture.

## Core design

### `Vehicle`

`Vehicle` owns the license plate and `SpotType`. Construction calls
`normalize_plate`, so all domain objects use one canonical Indian plate form:
`STATE-RTO-SERIES-NUMBER`, such as `KA-01-AB-1234`.

### `Spot`

`Spot` represents one physical location with a level, spot type, charger flag,
and occupancy metadata. EV spots are created with `has_charger=True`. The
`reservation_code`, `vehicle`, and `ticket_id` references make the current
physical state visible without searching history.

### `Ticket`

`Ticket` is the live or completed parking session. It records the vehicle,
spot, entry/exit timestamps, fee, generated ticket ID, and whether the session
was force-closed by the 24-hour sweep.

### `RateCard`

`RateCard` is isolated from garage allocation. It accepts already-clean numeric
rates and calculates a per-stay fee using `Decimal`. It rounds a partial hour
up and charges at least one first-hour rate, including for an immediate
checkout. Normal fees are capped by `daily_cap`.

### `Clock`

`Clock` is a small thread-safe injectable time source. It defaults to
`datetime.now()` but supports `set_time` and `advance_hours`, allowing tests
and `POST /clock` to exercise time-dependent behavior without waiting.

### `Reservation`

`Reservation` records a six-character confirmation code, canonical plate,
vehicle type, arrival window, payment deadline, status, held spot, payment
method, payment status, and displayed payment amount. `PaymentMethod` supports
UPI and cash; `PaymentStatus` distinguishes pending, paid, and collected.

### `ParkingGarage`

`ParkingGarage` owns spot construction and the state transitions for check-in,
checkout, reservations, auto-close, and transfer. A single `Lock` protects
allocation, releases, reservations, transfer, and history mutations.

## O(1)-style live lookups

The garage deliberately separates live state from completed history:

- `active_tickets: dict[str, Ticket]` maps canonical plate to the current car.
- `free_spots: dict[SpotType, set[str]]` tracks available physical spot IDs.
- `spots: dict[str, Spot]` resolves a spot ID to its level and state.
- `reservations: dict[str, Reservation]` indexes confirmation codes.
- `reservation_by_plate: dict[str, str]` enforces one active reservation per
  plate and supports plate lookup without scanning the log.
- `history: list[Ticket]` is append-only completed transaction history.

Check-in pops from a compatible free set; checkout returns the spot to that
set. Plate lookup, free-spot checks, and active-state checks do not scan
`history`, so historical growth does not make those operations linear in the
day's transaction count. Reservation lookup by plate is an index lookup; the
history list is not involved.

## T4 messy rate-card import

T4 is not implemented. The supplied material contained the literal placeholder
`<PASTE YOUR MESSY RATE CARD DATA HERE>`, not actual rows. Consequently:

- there is no `clean_rate_card` importer module;
- no duplicate-row or missing-field tie-break rule was applied;
- no broken-row behavior can honestly be documented;
- `build_garage()` still constructs `RateCard(50, 30, 200)`.

Inventing a cleaning rule or rates without source data would silently alter
billing. The correct next step is to add a separate parser with explicit tests
once the real payload is available. `RateCard` already receives clean values
and remains independent of parsing.

## T2 rolling-24-hour billing and `/clock`

`ParkingGarage.auto_close_overdue()` runs under the same lock as normal
checkout. It scans active tickets only, identifies sessions strictly over 24
hours, removes them from `active_tickets`, computes:

```text
ceil((clock_time - entry_time) / 24 hours) * daily_cap
```

and releases the spot. The ticket is appended to history with
`auto_closed=True`. A normal checkout removes the ticket first, so a later
sweep cannot process it twice.

`POST /clock` updates the shared `Clock`, runs the reservation no-show sweep,
then runs active-session auto-close, and returns the auto-closed tickets. It is
the test hook standing in for a nightly cron job.

The clock endpoint accepts either `set_time` or `advance_hours`, but not both.
The default garage and API clock are process-local, so restarting the server
resets the simulated time and all other state.

## T6 valet transfer

`ParkingGarage.transfer(old_plate, new_plate)` normalizes both plates and runs
under the garage lock. It verifies that the old plate is active and the new
plate is not. It then changes the ticket's plate and vehicle reference and
updates the occupied spot's vehicle reference. It never removes or re-adds the
spot in `free_spots`, so availability cannot flicker to free during transfer.
Entry time, spot ID, ticket ID, and subsequent fee calculation remain intact.
The API maps missing sessions to 404 and an occupied destination to 409.

## Reservation holds and no-show behavior

The intended reservation lifecycle is:

1. Create a near-term reservation. A normal UPI reservation starts as
   `pending_payment` and does not remove a spot from `free_spots`.
2. Confirm UPI payment. The backend atomically pops a compatible spot and marks
   the reservation paid.
3. Confirm cash-at-gate. The current payment endpoint can switch the
   reservation to cash and hold a compatible spot immediately; its payment
   status remains pending until the cash-collection endpoint is called.
4. Cancel, check in, or expire the reservation. Cancellation and expiration
   release a held spot; conversion changes the reserved spot into an occupied
   active ticket.

The shared `/clock` sweep applies the 30-minute no-show grace rule to paid
reservations: after an arrival window has ended by more than 30 minutes, the
reservation is expired and its held spot is released. Checked-in reservations
have status `checked_in` and are not touched. Keeping this in the same sweep
as T2 ensures one simulated nightly event updates both overdue cars and
no-show reservations atomically under the garage lock.

Important current-state detail: ordinary reservation reads and some garage
operations call the general expiry helper without the grace flag, while
`auto_close_overdue()` calls it with `apply_no_show_grace=True`. The documented
and tested grace-period mechanism is therefore specifically the `/clock`
sweep; this is an area to simplify if the expiry semantics are hardened later.

## Authentication design

`AuthStore` is an in-memory staff identity/session service:

- staff signup accepts name, email, and an eight-character minimum password;
- passwords are PBKDF2-HMAC-SHA256 hashes with per-user salts;
- login issues a signed, HTTP-only, SameSite cookie;
- logout removes the server-side session mapping;
- the attendant root redirects to `/login` after at least one account exists;
- `/api/auth/me` supplies the sidebar identity.

Customers deliberately remain guest users. A plate plus confirmation code is
sufficient for reservation lookup/cancellation, which avoids account friction
for a one-time parking visit. Optional customer email accounts are not built.

This is suitable for a demonstrator, not production authentication. The store
and sessions are process-local, the fallback signing secret is in code, and
most operational API routes are not protected by an auth dependency yet. A
production version should use a database, an environment secret, expiration,
CSRF protection, and route-level authorization.

## Known limitations and next steps

- T4 rate-card import is blocked on missing source data.
- No persistent database exists.
- No real UPI gateway or payment ledger exists.
- Cash collection is a state transition, not a financial reconciliation flow.
- Polling is used instead of WebSockets.
- The CLI supports core parking operations but not reservations or auth.
- Reports use process-local history and `datetime.now()` rather than a durable
  reporting clock.
- Add durable storage, migrations, role-based staff permissions, route-level
  auth dependencies, environment configuration, structured audit events, and
  integration/browser tests next.
