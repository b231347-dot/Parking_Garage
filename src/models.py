from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from decimal import Decimal
import re
from uuid import uuid4


class SpotType(str, Enum):
	COMPACT = "compact"
	STANDARD = "standard"
	EV = "ev"


class ReservationStatus(str, Enum):
	PENDING_PAYMENT = "pending_payment"
	PAID = "paid"
	CANCELLED = "cancelled"
	EXPIRED = "expired"
	CHECKED_IN = "checked_in"


class PaymentMethod(str, Enum):
	UPI = "upi"
	CASH = "cash"


class PaymentStatus(str, Enum):
	PENDING = "pending"
	PAID = "paid"
	COLLECTED = "collected"


PLATE_PATTERN = re.compile(r"^([A-Z]{2})-?(\d{2})-?([A-Z]{1,2})-?(\d{4})$")


def normalize_plate(plate: str) -> str:
	"""Validate and canonicalize an Indian registration plate."""
	compact = re.sub(r"[\s-]+", "", plate).upper()
	match = PLATE_PATTERN.fullmatch(compact)
	if match is None:
		raise ValueError(
			"invalid Indian plate; use 2 letters, 2 digits, 1-2 series letters, "
			"and 4 digits (for example KA01AB1234)"
		)
	return "-".join(match.groups())


@dataclass(frozen=True)
class Vehicle:
	plate: str
	vehicle_type: SpotType

	def __post_init__(self) -> None:
		object.__setattr__(self, "plate", normalize_plate(self.plate))
		if isinstance(self.vehicle_type, str):
			object.__setattr__(self, "vehicle_type", SpotType(self.vehicle_type.lower()))


@dataclass
class Spot:
	spot_id: str
	level: int
	spot_type: SpotType
	has_charger: bool = False
	occupied: bool = False
	vehicle: Vehicle | None = None
	ticket_id: str | None = None
	reservation_code: str | None = None


@dataclass
class Ticket:
	plate: str
	vehicle: Vehicle
	spot_id: str
	entry_time: datetime
	exit_time: datetime | None = None
	fee: Decimal | None = None
	ticket_id: str = ""
	auto_closed: bool = False

	def __post_init__(self) -> None:
		if not self.ticket_id:
			self.ticket_id = uuid4().hex[:12].upper()


@dataclass
class Reservation:
	confirmation_code: str
	plate: str
	vehicle_type: SpotType
	arrival_start: datetime
	arrival_end: datetime
	created_at: datetime
	payment_deadline: datetime
	status: ReservationStatus = ReservationStatus.PENDING_PAYMENT
	spot_id: str | None = None
	paid_at: datetime | None = None
	payment_method: PaymentMethod = PaymentMethod.UPI
	payment_status: PaymentStatus = PaymentStatus.PENDING
	payment_amount: Decimal | None = None
	upi_id: str | None = None

