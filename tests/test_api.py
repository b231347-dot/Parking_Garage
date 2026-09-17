from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src import api
from src.garage import ParkingGarage
from src.models import SpotType
from src.rate_card import RateCard


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
	monkeypatch.setattr(
		api,
		"garage",
		ParkingGarage(
			levels=1,
			spots_per_level={SpotType.COMPACT: 1, SpotType.STANDARD: 1, SpotType.EV: 1},
			rate_card=RateCard(5, 3, 25),
		),
	)
	return TestClient(api.app)


def test_dashboard_and_check_in(client: TestClient) -> None:
	response = client.get("/api/dashboard")
	assert response.status_code == 200
	assert response.json()["total"] == 3

	response = client.post(
		"/api/check-in",
		json={"plate": "ka01ab1234", "vehicle_type": "ev", "entry_time": "2026-01-01T08:00:00"},
	)
	assert response.status_code == 201
	assert response.json()["ticket"]["spot_id"].startswith("L1-EV")


def test_lookup_availability_and_checkout(client: TestClient) -> None:
	client.post(
		"/api/check-in",
		json={"plate": "MH12CD5678", "vehicle_type": "compact", "entry_time": "2026-01-01T08:00:00"},
	)
	assert client.get("/api/vehicle/mh12cd5678").status_code == 200
	assert client.get("/api/spots/availability/ev").json()["is_free"] is True
	checkout = client.post(
		"/api/check-out",
		json={"plate": "MH-12-CD-5678", "exit_time": "2026-01-01T09:01:00"},
	)
	assert checkout.status_code == 200
	assert checkout.json()["ticket"]["fee"] == "8.00"
	assert checkout.json()["ticket"]["plate"] == "MH-12-CD-5678"
	assert len(client.get("/api/transactions").json()["transactions"]) == 1


def test_invalid_and_unavailable_requests(client: TestClient) -> None:
	assert client.get("/api/vehicle/KA01ZZ9999").status_code == 404
	assert client.post("/api/check-out", json={"plate": "KA01ZZ9999"}).status_code == 404
	assert client.post("/api/check-in", json={"plate": "abc123", "vehicle_type": "ev"}).status_code == 400
	client.post("/api/check-in", json={"plate": "KA01AB1234", "vehicle_type": "ev"})
	assert client.post("/api/check-in", json={"plate": "DL03EF9012", "vehicle_type": "ev"}).status_code == 409


def test_reservation_payment_lookup_cancel_and_attendant_conversion(client: TestClient) -> None:
	start = datetime.now().replace(microsecond=0) + timedelta(minutes=10)
	end = start + timedelta(hours=2)
	response = client.post("/api/reservations", json={
		"plate": "KA01AB1234", "vehicle_type": "compact",
		"arrival_start": start.isoformat(), "arrival_end": end.isoformat(),
	})
	assert response.status_code == 201
	reservation = response.json()["reservation"]
	assert reservation["status"] == "pending_payment"
	assert client.get("/api/spots/availability/compact").json()["available"] == 1

	code = reservation["confirmation_code"]
	paid = client.post(f"/api/reservations/{code}/pay")
	assert paid.status_code == 200
	assert paid.json()["reservation"]["status"] == "paid"
	assert client.get(f"/api/reservations/lookup?code={code}").status_code == 200
	converted = client.post(f"/api/reservations/{code}/check-in")
	assert converted.status_code == 201
	assert converted.json()["ticket"]["plate"] == "KA-01-AB-1234"


def test_reservation_duplicate_and_cancel(client: TestClient) -> None:
	start = datetime.now().replace(microsecond=0) + timedelta(minutes=10)
	payload = {"plate": "MH12CD5678", "vehicle_type": "compact", "arrival_start": start.isoformat(), "arrival_end": (start + timedelta(hours=2)).isoformat()}
	first = client.post("/api/reservations", json=payload)
	assert first.status_code == 201
	assert client.post("/api/reservations", json=payload).status_code == 409
	code = first.json()["reservation"]["confirmation_code"]
	assert client.post(f"/api/reservations/{code}/cancel").status_code == 200


def test_clock_auto_closes_overdue_session(client: TestClient) -> None:
	client.post("/api/check-in", json={"plate": "KA01AB1234", "vehicle_type": "compact", "entry_time": "2026-01-01T08:00:00"})
	response = client.post("/clock", json={"set_time": "2026-01-02T14:00:00"})
	assert response.status_code == 200
	assert response.json()["auto_closed"][0]["auto_closed"] is True
	assert response.json()["auto_closed"][0]["fee"] == "50.00"


def test_transfer_endpoint_preserves_active_session(client: TestClient) -> None:
	client.post("/api/check-in", json={"plate": "KA01AB1234", "vehicle_type": "compact", "entry_time": "2026-01-01T08:00:00"})
	response = client.post("/transfer", json={"old_plate": "KA01AB1234", "new_plate": "MH12CD5678"})
	assert response.status_code == 200
	assert response.json()["ticket"]["plate"] == "MH-12-CD-5678"
	assert client.get("/api/vehicle/KA01AB1234").status_code == 404
	assert client.get("/api/vehicle/MH12CD5678").status_code == 200


def test_auth_signup_login_logout_and_customer_guest_access(client: TestClient) -> None:
	created = client.post("/api/auth/signup", json={"name": "Asha Rao", "email": "asha@example.com", "password": "securepass"})
	assert created.status_code == 200
	assert client.get("/api/auth/me").json()["user"]["name"] == "Asha Rao"
	assert client.post("/api/auth/logout").status_code == 200
	assert client.get("/api/auth/me").status_code == 401
	assert client.get("/customer").status_code == 200