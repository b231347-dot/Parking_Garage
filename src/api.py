from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .garage import (
	CarNotFoundError,
	NoSpotAvailableError,
	ParkingGarage,
	ReservationConflictError,
	ReservationNotFoundError,
	VehicleAlreadyParkedError,
)
from .auth import AuthStore
from .models import PaymentMethod, Reservation, ReservationStatus, SpotType, Ticket, Vehicle, normalize_plate
from .rate_card import RateCard


BASE_DIR = Path(__file__).resolve().parent.parent


def build_garage() -> ParkingGarage:
	return ParkingGarage(
		levels=3,
		spots_per_level={SpotType.COMPACT: 10, SpotType.STANDARD: 15, SpotType.EV: 5},
		rate_card=RateCard(50, 30, 200),
	)


garage = build_garage()
app = FastAPI(title="Parking Garage Attendant System", version="1.0.0")
auth = AuthStore()


class CheckInRequest(BaseModel):
	plate: str = Field(min_length=1, max_length=20)
	vehicle_type: SpotType
	entry_time: datetime | None = None


class CheckOutRequest(BaseModel):
	plate: str = Field(min_length=1, max_length=20)
	exit_time: datetime | None = None


class ReservationRequest(BaseModel):
	plate: str = Field(min_length=1, max_length=20)
	vehicle_type: SpotType
	arrival_start: datetime
	arrival_end: datetime
	payment_method: PaymentMethod = PaymentMethod.UPI
	upi_id: str | None = None


class StaffSignup(BaseModel):
	name: str = Field(min_length=1, max_length=80)
	email: str
	password: str = Field(min_length=8)


class StaffLogin(BaseModel):
	email: str
	password: str

class PaymentRequest(BaseModel):
	payment_method: PaymentMethod = PaymentMethod.UPI
	upi_id: str | None = None


class ClockRequest(BaseModel):
	set_time: datetime | None = None
	advance_hours: float | None = None


class TransferRequest(BaseModel):
	old_plate: str = Field(min_length=1, max_length=20)
	new_plate: str = Field(min_length=1, max_length=20)


def ticket_data(ticket: Ticket, include_exit: bool = False) -> dict[str, object]:
	spot = garage.spots[ticket.spot_id]
	data: dict[str, object] = {
		"ticket_id": ticket.ticket_id,
		"plate": ticket.plate,
		"vehicle_type": ticket.vehicle.vehicle_type.value,
		"spot_id": ticket.spot_id,
		"level": spot.level,
		"entry_time": ticket.entry_time.isoformat(),
		"status": "completed" if ticket.exit_time else "parked",
		"auto_closed": ticket.auto_closed,
	}
	if include_exit:
		data["exit_time"] = ticket.exit_time.isoformat() if ticket.exit_time else None
		data["fee"] = str(ticket.fee) if ticket.fee is not None else None
	return data


def reservation_data(reservation: Reservation) -> dict[str, object]:
	return {
		"confirmation_code": reservation.confirmation_code,
		"plate": reservation.plate,
		"vehicle_type": reservation.vehicle_type.value,
		"arrival_start": reservation.arrival_start.isoformat(),
		"arrival_end": reservation.arrival_end.isoformat(),
		"created_at": reservation.created_at.isoformat(),
		"payment_deadline": reservation.payment_deadline.isoformat(),
		"status": reservation.status.value,
		"spot_id": reservation.spot_id,
		"payment_method": reservation.payment_method.value,
		"payment_status": reservation.payment_status.value,
		"payment_amount": str(reservation.payment_amount) if reservation.payment_amount is not None else None,
	}


def error_response(error: Exception) -> HTTPException:
	if isinstance(error, VehicleAlreadyParkedError):
		return HTTPException(status_code=409, detail=str(error))
	if isinstance(error, NoSpotAvailableError):
		return HTTPException(status_code=409, detail=str(error))
	if isinstance(error, CarNotFoundError):
		return HTTPException(status_code=404, detail=str(error))
	if isinstance(error, ReservationNotFoundError):
		return HTTPException(status_code=404, detail=str(error))
	if isinstance(error, ReservationConflictError):
		return HTTPException(status_code=409, detail=str(error))
	return HTTPException(status_code=400, detail=str(error))


@app.get("/", response_class=FileResponse)
def index(request: Request) -> Response:
	if auth.users and auth.user_from_cookie(request.cookies.get("staff_session")) is None:
		return RedirectResponse("/login", status_code=303)
	return FileResponse(BASE_DIR / "templates" / "index.html")


@app.get("/login", response_class=FileResponse)
def login_page() -> FileResponse:
	return FileResponse(BASE_DIR / "templates" / "login.html")


@app.post("/api/auth/signup")
def signup(payload: StaffSignup, response: Response) -> dict[str, object]:
	try:
		user = auth.create_user(payload.name, payload.email, payload.password)
	except ValueError as error:
		raise HTTPException(status_code=409, detail=str(error)) from error
	response.set_cookie("staff_session", auth.sign_in(user), httponly=True, samesite="lax")
	return {"user": {"name": user.name, "email": user.email}}


@app.post("/api/auth/login")
def login(payload: StaffLogin, response: Response) -> dict[str, object]:
	user = auth.authenticate(payload.email, payload.password)
	if user is None:
		raise HTTPException(status_code=401, detail="invalid email or password")
	response.set_cookie("staff_session", auth.sign_in(user), httponly=True, samesite="lax")
	return {"user": {"name": user.name, "email": user.email}}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
	auth.sign_out(request.cookies.get("staff_session"))
	response.delete_cookie("staff_session")
	return {"ok": True}

@app.get("/api/auth/me")
def current_staff(request: Request) -> dict[str, object]:
	user = auth.user_from_cookie(request.cookies.get("staff_session"))
	if user is None:
		raise HTTPException(status_code=401, detail="not authenticated")
	return {"user": {"name": user.name, "email": user.email}}


@app.get("/customer", response_class=FileResponse)
def customer_portal() -> FileResponse:
	return FileResponse(BASE_DIR / "templates" / "customer.html")


@app.post("/api/check-in", status_code=201)
def check_in(payload: CheckInRequest) -> dict[str, object]:
	try:
		vehicle = Vehicle(payload.plate, payload.vehicle_type)
		return {"ticket": ticket_data(garage.check_in(vehicle, payload.entry_time))}
	except (ValueError, VehicleAlreadyParkedError, NoSpotAvailableError) as error:
		raise error_response(error) from error


@app.post("/api/check-out")
def check_out(payload: CheckOutRequest) -> dict[str, object]:
	try:
		return {"ticket": ticket_data(garage.check_out(normalize_plate(payload.plate), payload.exit_time), True)}
	except (ValueError, CarNotFoundError) as error:
		raise error_response(error) from error


@app.get("/api/vehicle/{plate}")
def vehicle_lookup(plate: str) -> dict[str, object]:
	try:
		normalized_plate = normalize_plate(plate)
	except ValueError as error:
		raise HTTPException(status_code=422, detail=str(error)) from error
	ticket = garage.find_car(normalized_plate)
	if ticket is None:
		raise HTTPException(status_code=404, detail="vehicle is not currently parked")
	return {"ticket": ticket_data(ticket)}


@app.get("/api/spots/availability")
def availability() -> dict[str, object]:
	garage.upcoming_reservations()
	counts = {spot_type.value: garage.available_spot_count(spot_type) for spot_type in SpotType}
	reserved = sum(reservation.status is ReservationStatus.PAID for reservation in garage.reservations.values())
	return {"available": counts, "total": len(garage.spots), "occupied": len(garage.active_tickets), "reserved": reserved}


@app.get("/api/spots/availability/{spot_type}")
def type_availability(spot_type: SpotType) -> dict[str, object]:
	return {"spot_type": spot_type.value, "available": garage.available_spot_count(spot_type), "is_free": garage.is_spot_free(spot_type)}


@app.get("/api/transactions")
def transactions(
	plate: str = "",
	from_date: date | None = None,
	to_date: date | None = None,
	page: int = Query(1, ge=1),
	page_size: int = Query(10, ge=1, le=100),
) -> dict[str, object]:
	try:
		normalized_filter = normalize_plate(plate) if plate else ""
	except ValueError as error:
		raise HTTPException(status_code=422, detail=str(error)) from error
	filtered = [ticket for ticket in reversed(garage.history) if (
		(not normalized_filter or normalized_filter in ticket.plate)
		and (from_date is None or ticket.entry_time.date() >= from_date)
		and (to_date is None or ticket.entry_time.date() <= to_date)
	)]
	start = (page - 1) * page_size
	page_items = filtered[start:start + page_size]
	return {
		"transactions": [ticket_data(ticket, True) for ticket in page_items],
		"page": page,
		"page_size": page_size,
		"total": len(filtered),
		"pages": max(1, (len(filtered) + page_size - 1) // page_size),
	}


@app.get("/api/dashboard")
def dashboard() -> dict[str, object]:
	availability_data = availability()
	return {
		**availability_data,
		"active_vehicles": len(garage.active_tickets),
		"completed_transactions": len(garage.history),
		"ev_available": garage.is_spot_free(SpotType.EV),
		"upcoming_reservations": [reservation_data(reservation) for reservation in garage.upcoming_reservations()],
	}


@app.get("/api/customer/config")
def customer_config() -> dict[str, object]:
	return {
		"rates": {
			"first_hour_rate": str(garage.rate_card.first_hour_rate),
			"additional_hour_rate": str(garage.rate_card.additional_hour_rate),
			"daily_cap": str(garage.rate_card.daily_cap),
		},
		"reservation_payment_minutes": 10,
	}


@app.post("/api/reservations", status_code=201)
def create_reservation(payload: ReservationRequest) -> dict[str, object]:
	try:
		reservation = garage.create_reservation(
			Vehicle(payload.plate, payload.vehicle_type),
			payload.arrival_start.replace(tzinfo=None),
			payload.arrival_end.replace(tzinfo=None),
			payment_method=payload.payment_method,
		)
		return {"reservation": reservation_data(reservation), "next_step": "payment"}
	except (ValueError, NoSpotAvailableError, ReservationConflictError) as error:
		raise error_response(error) from error


@app.post("/api/reservations/{confirmation_code}/pay")
def pay_reservation(confirmation_code: str, payload: PaymentRequest | None = None) -> dict[str, object]:
	try:
		request = payload or PaymentRequest()
		if payload is not None and request.payment_method is PaymentMethod.UPI and not request.upi_id:
			raise ValueError("mock UPI ID is required")
		return {"reservation": reservation_data(garage.pay_reservation(confirmation_code, payment_method=request.payment_method))}
	except (NoSpotAvailableError, ReservationConflictError, ReservationNotFoundError) as error:
		raise error_response(error) from error


@app.post("/api/reservations/{confirmation_code}/cash-collected")
def cash_collected(confirmation_code: str) -> dict[str, object]:
	try:
		return {"reservation": reservation_data(garage.mark_cash_collected(confirmation_code))}
	except (ReservationConflictError, ReservationNotFoundError) as error:
		raise error_response(error) from error


@app.get("/api/reservations/lookup")
def lookup_reservation(plate: str | None = None, code: str | None = None) -> dict[str, object]:
	if not plate and not code:
		raise HTTPException(status_code=422, detail="provide a plate or confirmation code")
	reservation = garage.find_reservation(code or plate or "")
	if reservation is None:
		raise HTTPException(status_code=404, detail="reservation not found")
	return {"reservation": reservation_data(reservation)}


@app.post("/api/reservations/{confirmation_code}/cancel")
def cancel_reservation(confirmation_code: str) -> dict[str, object]:
	try:
		return {"reservation": reservation_data(garage.cancel_reservation(confirmation_code))}
	except (ReservationConflictError, ReservationNotFoundError) as error:
		raise error_response(error) from error


@app.post("/api/reservations/{confirmation_code}/check-in", status_code=201)
def reservation_check_in(confirmation_code: str) -> dict[str, object]:
	try:
		return {"ticket": ticket_data(garage.convert_reservation_to_check_in(confirmation_code))}
	except (ReservationConflictError, ReservationNotFoundError, VehicleAlreadyParkedError) as error:
		raise error_response(error) from error


@app.get("/api/reservations")
def reservations() -> dict[str, object]:
	return {"reservations": [reservation_data(reservation) for reservation in garage.upcoming_reservations()]}


@app.post("/clock")
def clock(request: ClockRequest) -> dict[str, object]:
	if (request.set_time is None) == (request.advance_hours is None):
		raise HTTPException(status_code=422, detail="provide exactly one of set_time or advance_hours")
	try:
		if request.set_time is not None:
			current = garage.clock.set_time(request.set_time.replace(tzinfo=None))
		else:
			current = garage.clock.advance_hours(request.advance_hours or 0)
		closed = garage.auto_close_overdue(current)
		return {"clock_time": current.isoformat(), "auto_closed": [ticket_data(ticket, True) for ticket in closed]}
	except ValueError as error:
		raise error_response(error) from error


@app.post("/transfer")
def transfer(request: TransferRequest) -> dict[str, object]:
	try:
		return {"ticket": ticket_data(garage.transfer(request.old_plate, request.new_plate))}
	except (ValueError, CarNotFoundError, VehicleAlreadyParkedError) as error:
		raise error_response(error) from error


@app.get("/api/reports")
def reports() -> dict[str, object]:
	now = datetime.now()
	today = now.date()
	week_start = today - timedelta(days=today.weekday())
	completed = garage.history
	revenue_today = sum((ticket.fee or 0 for ticket in completed if ticket.exit_time and ticket.exit_time.date() == today), 0)
	revenue_week = sum((ticket.fee or 0 for ticket in completed if ticket.exit_time and ticket.exit_time.date() >= week_start), 0)
	stay_seconds = [
		(ticket.exit_time - ticket.entry_time).total_seconds()
		for ticket in completed
		if ticket.exit_time is not None
	]
	occupancy: dict[str, dict[str, float | int]] = {}
	for spot_type in SpotType:
		type_spots = [spot for spot in garage.spots.values() if spot.spot_type is spot_type]
		occupied = sum(spot.occupied for spot in type_spots)
		occupancy[spot_type.value] = {
			"occupied": occupied,
			"total": len(type_spots),
			"percentage": round((occupied / len(type_spots)) * 100, 1) if type_spots else 0,
		}
	return {
		"revenue_today": str(revenue_today),
		"revenue_week": str(revenue_week),
		"occupancy": occupancy,
		"average_stay_minutes": round(sum(stay_seconds) / len(stay_seconds) / 60, 1) if stay_seconds else 0,
	}


@app.get("/static/{asset_path:path}")
def static_asset(asset_path: str) -> FileResponse:
	path = BASE_DIR / "static" / asset_path
	if not path.is_file() or BASE_DIR not in path.resolve().parents:
		raise HTTPException(status_code=404, detail="asset not found")
	return FileResponse(path)