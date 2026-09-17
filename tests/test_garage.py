from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.garage import (
	CarNotFoundError,
	NoSpotAvailableError,
	ParkingGarage,
	ReservationConflictError,
	VehicleAlreadyParkedError,
)
from src.models import ReservationStatus, SpotType, Vehicle
from src.rate_card import RateCard


START = datetime(2026, 1, 1, 8, 0)


def make_garage(**counts: int) -> ParkingGarage:
	return ParkingGarage(
		levels=1,
		spots_per_level=counts,
		rate_card=RateCard("5.00", "3.00", "25.00"),
	)


def test_ev_vehicle_can_only_use_ev_spot() -> None:
	garage = make_garage(compact=1, standard=1, ev=1)
	ticket = garage.check_in(Vehicle("KA01AB1234", SpotType.EV), START)
	assert garage.spots[ticket.spot_id].spot_type is SpotType.EV


def test_ev_check_in_fails_when_ev_spots_are_full() -> None:
	garage = make_garage(compact=1, standard=1, ev=0)
	with pytest.raises(NoSpotAvailableError):
		garage.check_in(Vehicle("KA01AB1234", SpotType.EV), START)


def test_compact_overflows_into_standard() -> None:
	garage = make_garage(compact=0, standard=1, ev=0)
	ticket = garage.check_in(Vehicle("MH12CD5678", SpotType.COMPACT), START)
	assert garage.spots[ticket.spot_id].spot_type is SpotType.STANDARD


def test_standard_overflows_into_compact() -> None:
	garage = make_garage(compact=1, standard=0, ev=0)
	ticket = garage.check_in(Vehicle("DL03EF9012", SpotType.STANDARD), START)
	assert garage.spots[ticket.spot_id].spot_type is SpotType.COMPACT


def test_spot_exhaustion_is_clean() -> None:
	garage = make_garage(compact=1, standard=0, ev=0)
	garage.check_in(Vehicle("GJ05GH3456", SpotType.COMPACT), START)
	with pytest.raises(NoSpotAvailableError):
		garage.check_in(Vehicle("MH12CD5678", SpotType.COMPACT), START)


def test_no_double_assignment_or_duplicate_active_plate() -> None:
	garage = make_garage(compact=2, standard=0, ev=0)
	first = garage.check_in(Vehicle("KA01AB1234", SpotType.COMPACT), START)
	with pytest.raises(VehicleAlreadyParkedError):
		garage.check_in(Vehicle("KA01AB1234", SpotType.COMPACT), START)
	second = garage.check_in(Vehicle("MH12CD5678", SpotType.COMPACT), START)
	assert first.spot_id != second.spot_id


def test_checkout_releases_spot_and_records_history() -> None:
	garage = make_garage(compact=1, standard=0, ev=0)
	ticket = garage.check_in(Vehicle("UP16JK7890", SpotType.COMPACT), START)
	completed = garage.check_out("UP16JK7890", START + timedelta(hours=1))
	assert completed.fee == Decimal("5.00")
	assert garage.find_car("UP16JK7890") is None
	assert garage.is_spot_free(SpotType.COMPACT)
	assert garage.history == [ticket]


def test_active_lookup_and_availability_are_constant_time_indexes() -> None:
	garage = make_garage(compact=1, standard=0, ev=1)
	ticket = garage.check_in(Vehicle("RJ14LM1234", SpotType.EV), START)
	assert garage.find_car("RJ14LM1234") is ticket
	assert not garage.is_spot_free(SpotType.EV)
	garage.check_out("RJ14LM1234", START + timedelta(hours=1))
	assert garage.is_spot_free(SpotType.EV)


def test_missing_car_checkout_fails() -> None:
	garage = make_garage(compact=1)
	with pytest.raises(CarNotFoundError):
		garage.check_out("KA01AB1234", START)


def test_invalid_indian_plate_is_rejected() -> None:
	garage = make_garage(compact=1)
	with pytest.raises(ValueError, match="invalid Indian plate"):
		garage.check_in(Vehicle("abc123", SpotType.COMPACT), START)


def test_pending_reservation_does_not_reduce_free_count_until_payment() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(
		Vehicle("KA01AB1234", SpotType.COMPACT),
		START + timedelta(minutes=10),
		START + timedelta(hours=2),
		START,
	)
	assert reservation.status is ReservationStatus.PENDING_PAYMENT
	assert garage.available_spot_count(SpotType.COMPACT) == 1
	paid = garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	assert paid.status is ReservationStatus.PAID
	assert garage.available_spot_count(SpotType.COMPACT) == 0


def test_payment_expiration_releases_pending_reservation_and_allows_new_one() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(
		Vehicle("MH12CD5678", SpotType.COMPACT), START + timedelta(minutes=10), START + timedelta(hours=2), START
	)
	assert garage.find_reservation(reservation.confirmation_code, START + timedelta(minutes=11)).status is ReservationStatus.EXPIRED
	new_reservation = garage.create_reservation(
		Vehicle("DL03EF9012", SpotType.COMPACT), START + timedelta(minutes=20), START + timedelta(hours=2), START + timedelta(minutes=11)
	)
	assert new_reservation.status is ReservationStatus.PENDING_PAYMENT


def test_paid_reservation_expiration_releases_held_spot() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(
		Vehicle("GJ05GH3456", SpotType.COMPACT), START + timedelta(minutes=10), START + timedelta(minutes=30), START
	)
	garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	assert garage.available_spot_count(SpotType.COMPACT) == 0
	assert garage.find_reservation(reservation.confirmation_code, START + timedelta(minutes=31)).status is ReservationStatus.EXPIRED
	assert garage.available_spot_count(SpotType.COMPACT) == 1


def test_only_one_active_reservation_per_plate() -> None:
	garage = make_garage(compact=2)
	garage.create_reservation(Vehicle("RJ14LM1234", SpotType.COMPACT), START + timedelta(minutes=10), START + timedelta(hours=2), START)
	with pytest.raises(ReservationConflictError, match="already has an active reservation"):
		garage.create_reservation(Vehicle("RJ14LM1234", SpotType.COMPACT), START + timedelta(minutes=20), START + timedelta(hours=2), START)


def test_paid_reservation_can_be_cancelled_and_converted() -> None:
	garage = make_garage(compact=2)
	reservation = garage.create_reservation(Vehicle("UP16JK7890", SpotType.COMPACT), START + timedelta(minutes=10), START + timedelta(hours=2), START)
	garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	garage.cancel_reservation(reservation.confirmation_code, START + timedelta(minutes=2))
	assert garage.available_spot_count(SpotType.COMPACT) == 2

	second = garage.create_reservation(Vehicle("KA01AB1234", SpotType.COMPACT), START + timedelta(minutes=10), START + timedelta(hours=2), START)
	garage.pay_reservation(second.confirmation_code, START + timedelta(minutes=1))
	ticket = garage.convert_reservation_to_check_in(second.confirmation_code, START + timedelta(minutes=5))
	assert ticket.plate == "KA-01-AB-1234"
	assert second.status is ReservationStatus.CHECKED_IN


def test_auto_close_over_24_hours_bills_two_daily_caps_and_frees_spot() -> None:
	garage = make_garage(compact=1)
	ticket = garage.check_in(Vehicle("KA01AB1234", SpotType.COMPACT), START)
	closed = garage.auto_close_overdue(START + timedelta(hours=30))
	assert closed == [ticket]
	assert ticket.auto_closed is True
	assert ticket.fee == Decimal("50.00")
	assert garage.find_car("KA01AB1234") is None
	assert garage.is_spot_free(SpotType.COMPACT)


def test_auto_close_leaves_under_24_hour_session_untouched() -> None:
	garage = make_garage(compact=1)
	ticket = garage.check_in(Vehicle("MH12CD5678", SpotType.COMPACT), START)
	assert garage.auto_close_overdue(START + timedelta(hours=24)) == []
	assert garage.find_car("MH12CD5678") is ticket
	assert garage.history == []


def test_auto_close_processes_multiple_overdue_sessions_once() -> None:
	garage = make_garage(compact=2)
	first = garage.check_in(Vehicle("DL03EF9012", SpotType.COMPACT), START)
	second = garage.check_in(Vehicle("GJ05GH3456", SpotType.COMPACT), START + timedelta(hours=1))
	closed = garage.auto_close_overdue(START + timedelta(hours=49))
	assert {ticket.ticket_id for ticket in closed} == {first.ticket_id, second.ticket_id}
	assert {ticket.fee for ticket in closed} == {Decimal("75.00"), Decimal("50.00")}
	assert garage.auto_close_overdue(START + timedelta(hours=50)) == []
	assert len(garage.history) == 2


def test_normal_checkout_is_not_double_processed_by_auto_close() -> None:
	garage = make_garage(compact=1)
	ticket = garage.check_in(Vehicle("RJ14LM1234", SpotType.COMPACT), START)
	completed = garage.check_out("RJ14LM1234", START + timedelta(hours=1))
	assert completed is ticket
	assert garage.auto_close_overdue(START + timedelta(hours=48)) == []
	assert garage.history == [completed]


def test_transfer_preserves_spot_entry_time_and_fee() -> None:
	garage = make_garage(compact=1)
	ticket = garage.check_in(Vehicle("UP16JK7890", SpotType.COMPACT), START)
	spot_id = ticket.spot_id
	transferred = garage.transfer("UP16JK7890", "KA01AB1234")
	assert transferred.spot_id == spot_id
	assert transferred.entry_time == START
	assert garage.available_spot_count(SpotType.COMPACT) == 0
	completed = garage.check_out("KA01AB1234", START + timedelta(hours=1))
	assert completed.fee == Decimal("5.00")


def test_transfer_missing_or_duplicate_plate_does_not_mutate_sessions() -> None:
	garage = make_garage(compact=2)
	first = garage.check_in(Vehicle("KA01AB1234", SpotType.COMPACT), START)
	second = garage.check_in(Vehicle("MH12CD5678", SpotType.COMPACT), START)
	with pytest.raises(CarNotFoundError):
		garage.transfer("DL03EF9012", "GJ05GH3456")
	with pytest.raises(VehicleAlreadyParkedError):
		garage.transfer("KA01AB1234", "MH12CD5678")
	assert garage.find_car("KA01AB1234") is first
	assert garage.find_car("MH12CD5678") is second


def test_no_show_grace_keeps_reservation_at_20_minutes() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(Vehicle("KA01AB1234", SpotType.COMPACT), START + timedelta(minutes=5), START + timedelta(minutes=30), START)
	garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	garage.auto_close_overdue(START + timedelta(minutes=50))
	assert reservation.status is ReservationStatus.PAID
	assert garage.available_spot_count(SpotType.COMPACT) == 0


def test_no_show_grace_cancels_at_35_minutes_via_sweep() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(Vehicle("MH12CD5678", SpotType.COMPACT), START + timedelta(minutes=5), START + timedelta(minutes=30), START)
	garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	garage.auto_close_overdue(START + timedelta(minutes=65))
	assert reservation.status is ReservationStatus.EXPIRED
	assert garage.available_spot_count(SpotType.COMPACT) == 1


def test_checked_in_reservation_is_not_touched_by_no_show_sweep() -> None:
	garage = make_garage(compact=1)
	reservation = garage.create_reservation(Vehicle("DL03EF9012", SpotType.COMPACT), START + timedelta(minutes=5), START + timedelta(minutes=30), START)
	garage.pay_reservation(reservation.confirmation_code, START + timedelta(minutes=1))
	garage.convert_reservation_to_check_in(reservation.confirmation_code, START + timedelta(minutes=10))
	garage.auto_close_overdue(START + timedelta(minutes=65))
	assert reservation.status is ReservationStatus.CHECKED_IN
