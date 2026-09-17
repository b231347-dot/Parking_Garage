from datetime import datetime, timedelta
from decimal import Decimal
from collections.abc import Mapping
import secrets
import string
import math
from threading import Lock

from .clock import Clock
from .models import PaymentMethod, PaymentStatus, Reservation, ReservationStatus, Spot, SpotType, Ticket, Vehicle, normalize_plate
from .rate_card import RateCard


class GarageError(Exception):
	"""Base exception for expected garage operation failures."""


class VehicleAlreadyParkedError(GarageError):
	pass


class NoSpotAvailableError(GarageError):
	pass


class CarNotFoundError(GarageError):
	pass


class ReservationNotFoundError(GarageError):
	pass


class ReservationConflictError(GarageError):
	pass


class ParkingGarage:
	def __init__(
		self,
		levels: int,
		spots_per_level: Mapping[SpotType | str, int],
		rate_card: RateCard,
		clock: Clock | None = None,
	) -> None:
		if levels < 1:
			raise ValueError("levels must be at least 1")
		normalized_counts = {
			SpotType(spot_type.lower() if isinstance(spot_type, str) else spot_type): count
			for spot_type, count in spots_per_level.items()
		}
		if any(count < 0 for count in normalized_counts.values()):
			raise ValueError("spot counts must not be negative")

		self.rate_card = rate_card
		self.clock = clock or Clock()
		self.spots: dict[str, Spot] = {}
		self.free_spots: dict[SpotType, set[str]] = {
			spot_type: set() for spot_type in SpotType
		}
		for level in range(1, levels + 1):
			for spot_type in SpotType:
				for number in range(1, normalized_counts.get(spot_type, 0) + 1):
					spot_id = f"L{level}-{spot_type.value.upper()}-{number}"
					spot = Spot(
						spot_id=spot_id,
						level=level,
						spot_type=spot_type,
						has_charger=spot_type is SpotType.EV,
					)
					self.spots[spot_id] = spot
					self.free_spots[spot_type].add(spot_id)

		self.active_tickets: dict[str, Ticket] = {}
		self.history: list[Ticket] = []
		self.reservations: dict[str, Reservation] = {}
		self.reservation_by_plate: dict[str, str] = {}
		self._lock = Lock()

	def check_in(self, vehicle: Vehicle, entry_time: datetime | None = None) -> Ticket:
		with self._lock:
			self._expire_reservations(entry_time or self.clock.now())
			if vehicle.plate in self.active_tickets:
				raise VehicleAlreadyParkedError(f"{vehicle.plate} is already parked")

			candidate_types = self._spot_type_for(vehicle.vehicle_type)
			selected_type = next(
				(candidate for candidate in candidate_types if self.free_spots[candidate]), None
			)
			if selected_type is None:
				raise NoSpotAvailableError(
					f"no spot available for {vehicle.vehicle_type.value} vehicle"
				)

			selected_id = self.free_spots[selected_type].pop()
			spot = self.spots[selected_id]
			ticket = Ticket(
				plate=vehicle.plate,
				vehicle=vehicle,
				spot_id=selected_id,
				entry_time=entry_time or self.clock.now(),
			)
			spot.occupied = True
			spot.vehicle = vehicle
			spot.ticket_id = ticket.ticket_id
			self.active_tickets[vehicle.plate] = ticket
			return ticket

	def create_reservation(
		self,
		vehicle: Vehicle,
		arrival_start: datetime,
		arrival_end: datetime,
		now: datetime | None = None,
		payment_method: PaymentMethod = PaymentMethod.UPI,
	) -> Reservation:
		current = now or self.clock.now()
		with self._lock:
			self._expire_reservations(current)
			if vehicle.plate in self.active_tickets:
				raise ReservationConflictError(f"{vehicle.plate} is already parked")
			if vehicle.plate in self.reservation_by_plate:
				raise ReservationConflictError(f"{vehicle.plate} already has an active reservation")
			if arrival_end <= arrival_start or arrival_start < current:
				raise ValueError("arrival window must be in the future and end after it starts")
			if arrival_start > current + timedelta(hours=24) or arrival_end > current + timedelta(hours=24):
				raise ValueError("reservations are limited to the next 24 hours")
			if not any(self.free_spots[candidate] for candidate in self._spot_type_for(vehicle.vehicle_type)):
				raise NoSpotAvailableError(f"no spot available for {vehicle.vehicle_type.value} reservation")
			code = self._new_confirmation_code()
			reservation = Reservation(
				confirmation_code=code,
				plate=vehicle.plate,
				vehicle_type=vehicle.vehicle_type,
				arrival_start=arrival_start,
				arrival_end=arrival_end,
				created_at=current,
				payment_deadline=current + timedelta(minutes=10),
				payment_method=payment_method,
				payment_amount=self.rate_card.first_hour_rate,
			)
			self.reservations[code] = reservation
			self.reservation_by_plate[vehicle.plate] = code
			if payment_method is PaymentMethod.CASH:
				self._hold_reservation_spot(reservation, current)
			return reservation

	def pay_reservation(self, confirmation_code: str, now: datetime | None = None, payment_method: PaymentMethod = PaymentMethod.UPI) -> Reservation:
		current = now or self.clock.now()
		with self._lock:
			self._expire_reservations(current)
			reservation = self._reservation_or_error(confirmation_code)
			if payment_method is PaymentMethod.CASH:
				reservation.payment_method = PaymentMethod.CASH
				self._hold_reservation_spot(reservation, current)
				return reservation
			if reservation.payment_method is not PaymentMethod.UPI:
				raise ReservationConflictError("cash reservations are collected at the gate")
			if reservation.status is not ReservationStatus.PENDING_PAYMENT:
				raise ReservationConflictError(f"reservation is {reservation.status.value}")
			self._hold_reservation_spot(reservation, current)
			reservation.status = ReservationStatus.PAID
			reservation.payment_status = PaymentStatus.PAID
			return reservation

	def _hold_reservation_spot(self, reservation: Reservation, current: datetime) -> None:
		selected_type = next((candidate for candidate in self._spot_type_for(reservation.vehicle_type) if self.free_spots[candidate]), None)
		if selected_type is None:
			raise NoSpotAvailableError("no compatible spot remains for reservation")
		spot_id = self.free_spots[selected_type].pop()
		spot = self.spots[spot_id]
		spot.reservation_code = reservation.confirmation_code
		reservation.spot_id = spot_id
		reservation.paid_at = current
		if reservation.payment_method is PaymentMethod.CASH:
			reservation.status = ReservationStatus.PAID

	def mark_cash_collected(self, confirmation_code: str) -> Reservation:
		with self._lock:
			reservation = self._reservation_or_error(confirmation_code)
			if reservation.payment_method is not PaymentMethod.CASH:
				raise ReservationConflictError("only cash reservations need collection")
			reservation.payment_status = PaymentStatus.COLLECTED
			return reservation

	def cancel_reservation(self, confirmation_code: str, now: datetime | None = None) -> Reservation:
		with self._lock:
			self._expire_reservations(now or self.clock.now())
			reservation = self._reservation_or_error(confirmation_code)
			if reservation.status not in {ReservationStatus.PENDING_PAYMENT, ReservationStatus.PAID}:
				raise ReservationConflictError(f"reservation is {reservation.status.value}")
			self._release_reserved_spot(reservation)
			reservation.status = ReservationStatus.CANCELLED
			self.reservation_by_plate.pop(reservation.plate, None)
			return reservation

	def find_reservation(self, lookup: str, now: datetime | None = None) -> Reservation | None:
		with self._lock:
			self._expire_reservations(now or self.clock.now())
			code = lookup.strip().upper()
			if code not in self.reservations:
				try:
					code = self.reservation_by_plate.get(normalize_plate(lookup), "")
				except ValueError:
					return None
			return self.reservations.get(code)

	def convert_reservation_to_check_in(
		self, confirmation_code: str, entry_time: datetime | None = None
	) -> Ticket:
		current = entry_time or self.clock.now()
		with self._lock:
			self._expire_reservations(current)
			reservation = self._reservation_or_error(confirmation_code)
			if reservation.status is not ReservationStatus.PAID or not reservation.spot_id:
				raise ReservationConflictError("reservation must be paid before check-in")
			if reservation.plate in self.active_tickets:
				raise VehicleAlreadyParkedError(f"{reservation.plate} is already parked")
			spot = self.spots[reservation.spot_id]
			spot.reservation_code = None
			spot.occupied = True
			vehicle = Vehicle(reservation.plate, reservation.vehicle_type)
			ticket = Ticket(plate=vehicle.plate, vehicle=vehicle, spot_id=spot.spot_id, entry_time=current)
			spot.vehicle = vehicle
			spot.ticket_id = ticket.ticket_id
			self.active_tickets[vehicle.plate] = ticket
			reservation.status = ReservationStatus.CHECKED_IN
			self.reservation_by_plate.pop(reservation.plate, None)
			return ticket

	def upcoming_reservations(self, now: datetime | None = None) -> list[Reservation]:
		with self._lock:
			self._expire_reservations(now or self.clock.now())
			return [reservation for reservation in self.reservations.values() if reservation.status in {ReservationStatus.PENDING_PAYMENT, ReservationStatus.PAID}]

	def _expire_reservations(self, current: datetime, apply_no_show_grace: bool = False) -> None:
		for reservation in self.reservations.values():
			if reservation.status is ReservationStatus.PENDING_PAYMENT and current >= reservation.payment_deadline:
				reservation.status = ReservationStatus.EXPIRED
				self.reservation_by_plate.pop(reservation.plate, None)
			elif reservation.status is ReservationStatus.PAID and current > reservation.arrival_end + (timedelta(minutes=30) if apply_no_show_grace else timedelta(0)):
				self._release_reserved_spot(reservation)
				reservation.status = ReservationStatus.EXPIRED
				self.reservation_by_plate.pop(reservation.plate, None)

	def _release_reserved_spot(self, reservation: Reservation) -> None:
		if reservation.spot_id:
			spot = self.spots[reservation.spot_id]
			spot.reservation_code = None
			if not spot.occupied:
				self.free_spots[spot.spot_type].add(spot.spot_id)
			reservation.spot_id = None

	def _reservation_or_error(self, confirmation_code: str) -> Reservation:
		reservation = self.reservations.get(confirmation_code.strip().upper())
		if reservation is None:
			raise ReservationNotFoundError("reservation not found")
		return reservation

	def _new_confirmation_code(self) -> str:
		alphabet = string.ascii_uppercase + string.digits
		while True:
			code = "".join(secrets.choice(alphabet) for _ in range(6))
			if code not in self.reservations:
				return code

	def check_out(
		self, plate: str, exit_time: datetime | None = None
	) -> Ticket:
		with self._lock:
			ticket = self.active_tickets.pop(normalize_plate(plate), None)
			if ticket is None:
				raise CarNotFoundError(f"no active ticket for {plate}")
			ticket.exit_time = exit_time or self.clock.now()
			ticket.fee = self.rate_card.calculate_fee(ticket.entry_time, ticket.exit_time)
			spot = self.spots[ticket.spot_id]
			spot.occupied = False
			spot.vehicle = None
			spot.ticket_id = None
			self.free_spots[spot.spot_type].add(spot.spot_id)
			self.history.append(ticket)
			return ticket

	def auto_close_overdue(self, current_time: datetime | None = None) -> list[Ticket]:
		"""Close active sessions over 24 hours using rolling daily caps."""
		current = current_time or self.clock.now()
		with self._lock:
			self._expire_reservations(current, apply_no_show_grace=True)
			closed: list[Ticket] = []
			for plate, ticket in list(self.active_tickets.items()):
				if current - ticket.entry_time <= timedelta(hours=24):
					continue
				self.active_tickets.pop(plate)
				ticket.exit_time = current
				days = max(1, math.ceil((current - ticket.entry_time).total_seconds() / (24 * 60 * 60)))
				ticket.fee = (self.rate_card.daily_cap * Decimal(days)).quantize(Decimal("0.01"))
				ticket.auto_closed = True
				self._release_ticket_spot(ticket)
				self.history.append(ticket)
				closed.append(ticket)
			return closed

	def transfer(self, old_plate: str, new_plate: str) -> Ticket:
		old_key = normalize_plate(old_plate)
		new_key = normalize_plate(new_plate)
		with self._lock:
			ticket = self.active_tickets.get(old_key)
			if ticket is None:
				raise CarNotFoundError(f"no active ticket for {old_plate}")
			if new_key in self.active_tickets:
				raise VehicleAlreadyParkedError(f"{new_key} is already parked")
			vehicle = Vehicle(new_key, ticket.vehicle.vehicle_type)
			ticket.plate = vehicle.plate
			ticket.vehicle = vehicle
			self.active_tickets.pop(old_key)
			self.active_tickets[new_key] = ticket
			spot = self.spots[ticket.spot_id]
			spot.vehicle = vehicle
			return ticket

	def _release_ticket_spot(self, ticket: Ticket) -> None:
		spot = self.spots[ticket.spot_id]
		spot.occupied = False
		spot.vehicle = None
		spot.ticket_id = None
		self.free_spots[spot.spot_type].add(spot.spot_id)

	def find_car(self, plate: str) -> Ticket | None:
		return self.active_tickets.get(normalize_plate(plate))

	def is_spot_free(self, spot_type: SpotType | str) -> bool:
		normalized_type = SpotType(spot_type.lower() if isinstance(spot_type, str) else spot_type)
		return bool(self.free_spots[normalized_type])

	def available_spot_count(self, spot_type: SpotType | str) -> int:
		normalized_type = SpotType(spot_type.lower() if isinstance(spot_type, str) else spot_type)
		return len(self.free_spots[normalized_type])

	@staticmethod
	def _spot_type_for(vehicle_type: SpotType) -> list[SpotType]:
		if vehicle_type is SpotType.EV:
			return [SpotType.EV]
		if vehicle_type is SpotType.COMPACT:
			return [SpotType.COMPACT, SpotType.STANDARD]
		return [SpotType.STANDARD, SpotType.COMPACT]
