from datetime import datetime
from decimal import Decimal

from .garage import CarNotFoundError, GarageError, ParkingGarage
from .models import SpotType, Vehicle
from .rate_card import RateCard


def build_default_garage() -> ParkingGarage:
	return ParkingGarage(
		levels=2,
		spots_per_level={SpotType.COMPACT: 10, SpotType.STANDARD: 15, SpotType.EV: 5},
		rate_card=RateCard(Decimal("5.00"), Decimal("3.00"), Decimal("25.00")),
	)


def main() -> None:
	garage = build_default_garage()
	print("Parking garage CLI. Commands: in, out, free, find, quit")
	while True:
		try:
			command = input("> ").strip().lower()
		except (EOFError, KeyboardInterrupt):
			print()
			break
		try:
			if command in {"quit", "exit"}:
				break
			if command == "in":
				plate = input("Plate (e.g. KA01AB1234): ").strip()
				vehicle_type = SpotType(input("Type (compact/standard/ev): ").strip().lower())
				ticket = garage.check_in(Vehicle(plate, vehicle_type))
				print(f"Checked in at {ticket.spot_id} ({ticket.entry_time.isoformat()})")
			elif command == "out":
				ticket = garage.check_out(input("Plate: ").strip())
				print(f"Fee: ₹{ticket.fee:.2f}")
			elif command == "free":
				vehicle_type = SpotType(input("Type (compact/standard/ev): ").strip().lower())
				print("yes" if garage.is_spot_free(vehicle_type) else "no")
			elif command == "find":
				ticket = garage.find_car(input("Plate (e.g. KA01AB1234): ").strip())
				print(f"{ticket.spot_id} since {ticket.entry_time.isoformat()}" if ticket else "Not parked")
			else:
				print("Unknown command")
		except (ValueError, GarageError) as error:
			print(f"Error: {error}")


if __name__ == "__main__":
	main()
