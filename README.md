# Parking Garage Attendant System

A complete FastAPI web application and CLI for a configurable parking garage
with tiered fees, EV-only charging spots, compact/standard overflow, and
constant-time-style live lookups.

## Features

- Live check-in, check-out, plate lookup, availability, and transaction history.
- Indian registration plate validation and canonical formatting.
- Separate attendant and customer portals with mock-payment reservations.
- Staff signup/login uses PBKDF2 password hashes and signed HTTP-only session cookies. Customer reservations remain guest-accessible.
- Seven-second polling keeps both portals current without a manual refresh.
- Responsive dashboard served by FastAPI with vanilla HTML, CSS, and JavaScript.
- Atomic in-memory spot assignment protected for concurrent requests.
- Configurable levels, spot counts, rates, and per-stay daily cap.

## Policies

- Partial hours round up: a stay of 1 hour and 1 minute is charged as 2 hours.
- The first charged hour uses `first_hour_rate`; every later charged hour uses
	`additional_hour_rate`.
- Fees are displayed in Indian Rupees (`₹`); fee amounts remain numeric
	internally.
- `daily_cap` is a per-stay maximum. It applies to the elapsed stay from entry
	to exit and is not tied to the calendar date.
- Compact vehicles prefer compact spots and overflow into standard spots when
	needed. Standard vehicles do the reverse. Neither type can use EV spots.
- EV vehicles may use only EV spots.
- Plates accept `KA01AB1234` or `MH-12-CD-5678` style input: two state letters,
  two RTO digits, one or two series letters, and four digits. Input is
  uppercased and stored canonically as `STATE-RTO-SERIES-NUMBER`, for example
  `KA-01-AB-1234`.

## Reservations and customer portal

Open `/customer` for the customer-only portal. It shows live availability, the
₹ rate card, a near-term arrival-window form, mock payment, and reservation
lookup/cancellation. The attendant dashboard remains at `/` and shows paid
upcoming reservations with a check-in conversion action.

- A reservation is limited to the next 24 hours and has a specific arrival
	window.
- A pending reservation does not remove a spot from availability. It has 10
	minutes to complete mock payment.
- After payment, one compatible spot is held until the arrival window ends.
- Expired or cancelled reservations release their held spot immediately.
- A plate may have only one active pending or paid reservation.
- Confirmation codes are six uppercase letters/digits, such as `PK4X9B`.
- Late arrivals are treated as normal walk-ins after the reservation expires.
- No real payment provider is used; mock payment only changes reservation state.
- UPI reservations require the explicit mock UPI step before a spot is held.
- Cash reservations hold the spot immediately and remain `payment_status: pending`
	until the attendant collects cash at the gate.

The portals use short-interval polling every seven seconds because it is a
small, reliable addition to the existing FastAPI app and avoids introducing a
WebSocket connection manager for this in-memory version.

When the shared `/clock` sweep runs, paid or cash-pending reservations whose
arrival window ended more than 30 minutes ago are auto-cancelled as no-shows
and their held spots are released. A reservation checked in before that grace
period is never cancelled by the sweep.

## Nightly auto-close and test clock

`POST /clock` is a test hook that stands in for a real nightly cron job. Send
either `{"set_time":"2026-09-18T14:00:00"}` or
`{"advance_hours":25}`. The endpoint updates the shared injectable clock and
immediately sweeps active sessions.

Any session parked for more than 24 hours is force-closed. Auto-close billing
uses rolling 24-hour periods from entry, with one configured daily cap per
period: a 30-hour stay is billed as 2 daily caps. The spot is freed, the ticket
is added to history with `auto_closed: true`, and a later sweep cannot process
it again. Normal checkout uses the same clock source and lock.

## Valet transfer

`POST /transfer` accepts `{"old_plate":"KA01AB1234","new_plate":"MH12CD5678"}`.
It changes the active ticket's plate while preserving its spot and entry time.
The transfer is atomic, never releases the spot, and rejects a missing source
or an already-active destination plate.

## Rate-card import status

The requested dirty rate-card payload was not included in the supplied
specification; it still contains the literal placeholder
`<PASTE YOUR MESSY RATE CARD DATA HERE>`. The current configured rates remain
the existing sample values until the real payload is provided. No rates were
invented during this change, because doing so would silently change billing.

## Run tests

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -v
```

## Run the web application

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

In GitHub Codespaces, open the forwarded port `8000` in the Ports panel. The
root page is the attendant dashboard.

You can also run `python3 run.py`.

## API

- `POST /api/check-in` with `{"plate":"KA01AB1234","vehicle_type":"ev"}`
- `POST /api/check-out` with `{"plate":"KA-01-AB-1234"}`
- `GET /api/vehicle/{plate}`
- `GET /api/spots/availability`
- `GET /api/spots/availability/{spot_type}`
- `GET /api/transactions`
- `GET /api/dashboard`
- `GET /customer`
- `GET /api/customer/config`
- `POST /api/reservations`
- `POST /api/reservations/{code}/pay`
- `GET /api/reservations/lookup?plate=...` or `?code=...`
- `POST /api/reservations/{code}/cancel`
- `POST /api/reservations/{code}/check-in`
- `GET /api/reservations`
- `POST /clock`
- `POST /transfer`

The API returns `409` for duplicate check-ins and unavailable compatible spots,
`404` for missing active vehicles, and `422` for invalid request data.

## Architecture

`models.py`, `rate_card.py`, and `garage.py` contain framework-independent
business logic. `api.py` adapts those objects to HTTP. The browser dashboard
uses `fetch()` and updates only affected regions after each action. Active
vehicles use `dict[plate, Ticket]`; free spots use
`dict[SpotType, set[spot_id]]`; completed transactions stay in a separate
append-only list. These live operations do not scan transaction history.

## Run the CLI

```bash
python -m src.cli
```

The default CLI configuration is only an example. `ParkingGarage` accepts the
number of levels, spots per level by type, and a `RateCard`, so applications can
provide any garage configuration.

## Design

Currently parked vehicles are stored in `active_tickets`, indexed by plate.
Free spots are indexed by `SpotType` and spot ID. Completed tickets are appended
to the separate `history` list, so historical log growth does not require scans
for check-in, checkout, plate lookup, or availability queries.

## Stretch features

1. Persist completed transactions and restore active state after a restart.
2. Add reservations and a queue for full spot types.
3. Add daily reports for revenue, occupancy, and average stay duration.