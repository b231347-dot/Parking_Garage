# Parking Garage Attendant System

A FastAPI parking garage application with two browser portals and a small
Python CLI:

- `/`: attendant control room for check-in, checkout, search, reports, and
  upcoming reservations.
- `/customer`: guest customer portal for live availability, rate transparency,
  near-term reservations, mock payment, lookup, and cancellation.

The core parking logic is framework-independent Python. The current application
uses in-memory state; restarting the process resets active cars, reservations,
staff users, sessions, and transaction history.

## Features and policies

- Configurable levels, spot counts, and rates through `ParkingGarage`.
- Compact vehicles prefer compact spots and overflow into standard spots.
- Standard vehicles prefer standard spots and overflow into compact spots.
- Neither compact nor standard vehicles can use EV spots.
- EV vehicles can use only EV spots.
- Free spots are indexed by `SpotType`; active cars are indexed by canonical
  plate.
- Indian plates accept formats such as `KA01AB1234` and `MH-12-CD-5678`.
  They are uppercased and stored as `KA-01-AB-1234`.
- Fees are displayed in Indian Rupees (`₹`). Values remain numeric `Decimal`
  values internally.
- Partial hours always round up. A zero-duration or one-minute stay charges at
  least the first-hour rate.
- The normal `RateCard` daily cap limits one continuous parking stay.
- The `/clock` auto-close sweep force-closes active stays over 24 hours. Its
  billing uses rolling 24-hour periods from entry: a 30-hour stay is billed as
  two daily caps, and a 49-hour stay as three daily caps.

## Setup in Codespaces

From the repository root:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -v
python3 -m uvicorn src.api:app --host 0.0.0.0 --port 8000
```

The alternative launcher is:

```bash
python3 run.py
```

Open port `8000` in the VS Code Codespaces **Ports** panel. The URLs are:

- Attendant portal: `http://localhost:8000/`
- Staff login: `http://localhost:8000/login`
- Customer portal: `http://localhost:8000/customer`

The browser portals poll the backend every seven seconds instead of using
WebSockets. This is intentionally simple for the in-memory FastAPI deployment.

## Configuration

There are currently no required environment variables. The default application
configuration is defined in `src/api.py`:

- 3 levels
- 10 compact spots per level
- 15 standard spots per level
- 5 EV spots per level
- first hour: `50`
- additional hour: `30`
- daily cap: `200`

`src/auth.py` currently uses an in-memory `AuthStore` and a development fallback
signing secret. The secret is not read from an environment variable yet. Do not
use this authentication setup as production security without moving users,
sessions, and the signing secret to durable, environment-based configuration.

## Portals

### Attendant portal

The attendant dashboard provides check-in, checkout, plate lookup, availability,
reports, transaction history, an upcoming-reservation queue, and conversion of a
paid reservation into an active check-in. It also exposes the staff identity in
the sidebar after login.

Staff can create an account and sign in at `/login`. Passwords are stored as
PBKDF2-HMAC-SHA256 hashes. Successful signup/login sets an HTTP-only,
SameSite cookie named `staff_session`.

### Customer portal

The customer portal is intentionally guest-first. Customers do not need an
account to reserve, pay, find, or cancel a reservation. The current UI supports:

- live compact, standard, and EV availability;
- the current ₹ rate card;
- Indian plate input and a same-day/near-term arrival window;
- explicit UPI or cash-at-gate selection;
- mock UPI ID validation and confirmation;
- reservation confirmation details immediately after payment/confirmation;
- lookup by plate or six-character confirmation code;
- cancellation before check-in.

Customer email accounts are not implemented. This keeps one-time parking
low-friction, as requested.

## Reservation rules

- Confirmation codes are six uppercase letters/digits.
- A plate can have at most one active pending or paid reservation.
- Reservations are limited to the next 24 hours.
- A pending UPI reservation does not consume a free spot. The payment window is
  10 minutes.
- UPI payment holds a compatible spot only after the mock payment succeeds.
- Cash confirmation holds a compatible spot and reports `payment_status: pending`;
  cash collection is represented by `POST /api/reservations/{code}/cash-collected`.
- Paid/cash-held reservations release their spot on cancellation or expiration.
- `POST /clock` applies the no-show rule as part of the same sweep as active-car
  auto-close: a reservation whose arrival window ended more than 30 minutes ago
  is expired and its held spot is released. A checked-in reservation is not
  cancelled by this sweep.

The current code also invokes reservation expiry during some ordinary garage
operations. For exact grace-period testing, use `/clock`, which is the intended
nightly sweep hook and is covered by the tests.

## API reference

### Pages and static assets

- `GET /` - attendant dashboard; redirects to `/login` after at least one staff
  account exists and the request has no valid staff cookie.
- `GET /login` - staff login page.
- `GET /customer` - customer portal; no staff authentication required.
- `GET /static/{asset_path}` - CSS and JavaScript assets.

### Authentication

- `POST /api/auth/signup`
  - body: `{"name":"Asha Rao","email":"asha@example.com","password":"securepass"}`
- `POST /api/auth/login`
  - body: `{"email":"asha@example.com","password":"securepass"}`
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Live garage operations

- `POST /api/check-in`
  - body: `{"plate":"KA01AB1234","vehicle_type":"ev"}`
- `POST /api/check-out`
  - body: `{"plate":"KA-01-AB-1234"}`
- `GET /api/vehicle/{plate}`
- `GET /api/spots/availability`
- `GET /api/spots/availability/{compact|standard|ev}`
- `GET /api/transactions`
  - optional `plate`, `from_date`, `to_date`, `page`, and `page_size` query
    parameters.
- `GET /api/dashboard`
- `GET /api/reports`

### Customer and reservations

- `GET /api/customer/config` - current rate card and 10-minute payment window.
- `POST /api/reservations`
  - body includes `plate`, `vehicle_type`, `arrival_start`, `arrival_end`, and
    optional `payment_method` (`upi` or `cash`).
- `POST /api/reservations/{code}/pay`
  - optional body: `{"payment_method":"upi","upi_id":"name@bank"}`.
  - A no-body request remains supported for backward compatibility in the
    current test suite.
- `GET /api/reservations/lookup?plate=...` or `?code=...`
- `POST /api/reservations/{code}/cancel`
- `POST /api/reservations/{code}/check-in`
- `POST /api/reservations/{code}/cash-collected`
- `GET /api/reservations`

### Maintenance and valet operations

- `POST /clock`
  - body is exactly one of `{"set_time":"2026-09-18T14:00:00"}` or
    `{"advance_hours":25}`.
  - Returns the updated time and tickets auto-closed in that sweep.
- `POST /transfer`
  - body: `{"old_plate":"KA01AB1234","new_plate":"MH12CD5678"}`.
  - Keeps the same spot and entry time without releasing the spot.

## Errors and debugging

Common status codes:

- `400`: invalid plate, invalid time/rate input, or another expected domain
  validation error.
- `401`: invalid staff credentials or unauthenticated `/api/auth/me`.
- `404`: missing active vehicle or reservation.
- `409`: duplicate active plate, no compatible spot, duplicate reservation,
  conflicting reservation state, or occupied transfer destination.
- `422`: malformed request validation, invalid clock command, or malformed
  lookup/filter input.

Useful commands:

```bash
# Run everything
python3 -m pytest -v

# Run one layer
python3 -m pytest tests/test_rate_card.py -q
python3 -m pytest tests/test_garage.py -q
python3 -m pytest tests/test_api.py -q

# Check syntax
python3 -m compileall -q src tests
```

To reset all in-memory state, stop and restart Uvicorn. That clears the garage,
reservations, staff users, sessions, clock, and history. Tests isolate their
`ParkingGarage` instances and monkeypatch the API garage fixture.

If `/` redirects unexpectedly, sign up or log in at `/login`. If a plate is
rejected, use two state letters, two RTO digits, one or two series letters, and
four digits, with optional hyphens. If a payment hold is unavailable, check
`/api/spots/availability` and the reservation status returned by the API.

## CLI

The alternate CLI remains in `src/cli.py`:

```bash
python3 -m src.cli
```

It supports check-in, checkout, availability, active-car lookup, and exit. It is
separate from both browser portals and is retained as a useful development and
operations interface.

## Rate-card import status

No messy rate-card payload was present in the project or supplied specification;
the earlier placeholder was literally `<PASTE YOUR MESSY RATE CARD DATA HERE>`.
Therefore no T4 cleaning module was invented and no production rates were
silently changed. The active app still constructs `RateCard(50, 30, 200)` in
`build_garage()`. A real importer should be added only after the actual source
payload is supplied.

## Known limitations and next steps

- State is process-local and disappears on restart; add a database and durable
  migrations for production.
- Staff users and sessions are in memory; move them to durable storage and read
  the signing secret from an environment variable.
- Most operational API routes remain callable without a staff cookie for
  backward compatibility; only the attendant HTML root is redirect-gated when
  accounts exist.
- Mock UPI and cash collection have no real payment provider or audit ledger.
- The CLI does not include reservations or staff authentication.
- Polling is used instead of WebSockets.
- The rate-card importer is still pending the real dirty input data.
- Add persistent reservations, role-based staff permissions, CSRF protection,
  rate-card versioning, and structured audit logging next.
