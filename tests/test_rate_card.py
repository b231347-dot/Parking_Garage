from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.rate_card import RateCard


START = datetime(2026, 1, 1, 8, 0)


def test_exactly_one_hour() -> None:
	card = RateCard("5.00", "3.00", "25.00")
	assert card.calculate_fee(START, START + timedelta(hours=1)) == Decimal("5.00")


def test_near_instant_stay_charges_first_hour_rate() -> None:
	card = RateCard("5.00", "3.00", "25.00")
	assert card.calculate_fee(START, START) == Decimal("5.00")
	assert card.calculate_fee(START, START + timedelta(minutes=1)) == Decimal("5.00")


def test_partial_hour_rounds_up() -> None:
	card = RateCard("5.00", "3.00", "25.00")
	assert card.calculate_fee(START, START + timedelta(hours=1, minutes=1)) == Decimal("8.00")


def test_multiple_hours_use_tiered_rates() -> None:
	card = RateCard("5.00", "3.00", "25.00")
	assert card.calculate_fee(START, START + timedelta(hours=4)) == Decimal("14.00")


def test_fee_is_capped() -> None:
	card = RateCard("5.00", "3.00", "10.00")
	assert card.calculate_fee(START, START + timedelta(hours=10)) == Decimal("10.00")


def test_exit_before_entry_is_invalid() -> None:
	card = RateCard("5.00", "3.00", "25.00")
	with pytest.raises(ValueError):
		card.calculate_fee(START, START - timedelta(minutes=1))
