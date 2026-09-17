from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING


class RateCard:
	"""Tiered per-stay pricing with a configurable maximum charge."""

	def __init__(
		self,
		first_hour_rate: Decimal | int | str | float,
		additional_hour_rate: Decimal | int | str | float,
		daily_cap: Decimal | int | str | float,
	) -> None:
		self.first_hour_rate = Decimal(str(first_hour_rate))
		self.additional_hour_rate = Decimal(str(additional_hour_rate))
		self.daily_cap = Decimal(str(daily_cap))
		if min(self.first_hour_rate, self.additional_hour_rate, self.daily_cap) < 0:
			raise ValueError("rates and daily_cap must not be negative")

	def calculate_fee(self, entry_time: datetime, exit_time: datetime) -> Decimal:
		if exit_time < entry_time:
			raise ValueError("exit_time must not precede entry_time")
		duration = exit_time - entry_time
		charged_hours = max(1, int(
			(Decimal(duration.total_seconds()) / Decimal(timedelta(hours=1).total_seconds()))
			.to_integral_value(rounding=ROUND_CEILING)
		))

		fee = self.first_hour_rate
		if charged_hours > 1:
			fee += Decimal(charged_hours - 1) * self.additional_hour_rate
		return min(fee, self.daily_cap).quantize(Decimal("0.01"))
