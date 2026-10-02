"""Management commands.

python -m app.cli create-admin --email admin@example.com --phone 9000000000
python -m app.cli seed-demo
python -m app.cli expire-holds
"""

import argparse
import getpass
import sys
from datetime import time
from decimal import Decimal

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Amenity, Turf, User, UserRole
from app.security import hash_password
from app.services.bookings import expire_stale_holds, utcnow
from app.services.turfs import unique_slug

DEMO_PASSWORD = "Demo@12345"
DEMO_AMENITIES = ["Parking", "Floodlights", "Changing room", "Drinking water", "Washroom", "Cafeteria"]


def create_admin(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Admin password: ")
    if len(password) < 8:
        print("Password must be at least 8 characters", file=sys.stderr)
        return 1
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == args.email.lower())):
            print(f"A user with email {args.email} already exists", file=sys.stderr)
            return 1
        db.add(
            User(
                first_name=args.first_name,
                last_name=args.last_name,
                email=args.email.lower(),
                phone=args.phone,
                password_hash=hash_password(password),
                role=UserRole.admin,
                accepted_terms_at=utcnow(),
            )
        )
        db.commit()
    print(f"Admin {args.email} created")
    return 0


def _get_or_create_user(db, email: str, phone: str, first: str, role: UserRole) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            first_name=first,
            last_name="Demo",
            email=email,
            phone=phone,
            password_hash=hash_password(DEMO_PASSWORD),
            role=role,
            accepted_terms_at=utcnow(),
        )
        db.add(user)
        db.flush()
    return user


def seed_demo(args: argparse.Namespace) -> int:
    if get_settings().environment == "production" and not args.force:
        print("Refusing to seed demo data in production (use --force)", file=sys.stderr)
        return 1
    with SessionLocal() as db:
        amenities = {}
        for name in DEMO_AMENITIES:
            amenity = db.scalar(select(Amenity).where(Amenity.name == name))
            if amenity is None:
                amenity = Amenity(name=name)
                db.add(amenity)
                db.flush()
            amenities[name] = amenity

        _get_or_create_user(db, "admin@turfslot.demo", "9000000001", "Admin", UserRole.admin)
        owner = _get_or_create_user(db, "owner@turfslot.demo", "9000000002", "Owner", UserRole.owner)
        _get_or_create_user(db, "player@turfslot.demo", "9000000003", "Player", UserRole.user)

        demo_turfs = [
            (
                "Kickoff Arena",
                "football",
                "Kakkanad, Kochi",
                "Infopark Road",
                "10.015900",
                "76.341900",
                1200,
                40,
                20,
                14,
                "artificial",
                time(6),
                time(23),
                ["Parking", "Floodlights", "Changing room"],
            ),
            (
                "Boundary Box",
                "cricket",
                "Edappally, Kochi",
                "NH 66, near Lulu Mall",
                "10.025300",
                "76.308300",
                1500,
                50,
                30,
                22,
                "synthetic",
                time(6),
                time(1),
                ["Parking", "Floodlights", "Cafeteria"],
            ),
            (
                "Smash Court",
                "badminton",
                "Panampilly Nagar, Kochi",
                "Avenue Road",
                "9.961900",
                "76.294700",
                400,
                13,
                6,
                4,
                "hard",
                time(5),
                time(22),
                ["Drinking water", "Washroom"],
            ),
        ]
        created = 0
        for (
            name,
            sport,
            location,
            address,
            lat,
            lng,
            price,
            length,
            width,
            cap,
            surface,
            opens,
            closes,
            ams,
        ) in demo_turfs:
            if db.scalar(select(Turf.id).where(Turf.name == name, Turf.owner_id == owner.id)):
                continue
            turf = Turf(
                owner_id=owner.id,
                name=name,
                slug=unique_slug(db, name, location),
                sport_type=sport,
                description=f"{name} - a demo {sport} turf.",
                location=location,
                address=address,
                latitude=Decimal(lat),
                longitude=Decimal(lng),
                length_m=length,
                width_m=width,
                capacity=cap,
                surface_type=surface,
                price_per_hour=Decimal(price),
                opening_time=opens,
                closing_time=closes,
                is_verified=True,
            )
            turf.amenities = [amenities[a] for a in ams]
            db.add(turf)
            created += 1
        db.commit()
    print(f"Demo data ready ({created} turfs created). Logins (password {DEMO_PASSWORD}):")
    print("  admin@turfslot.demo, owner@turfslot.demo, player@turfslot.demo")
    return 0


def expire_holds(_: argparse.Namespace) -> int:
    with SessionLocal() as db:
        released = expire_stale_holds(db, utcnow())
        db.commit()
    print(f"Released {released} expired hold(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create an admin account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--phone", required=True)
    admin.add_argument("--first-name", default="Admin")
    admin.add_argument("--last-name", default="User")
    admin.add_argument("--password", help="Prompted for if omitted")
    admin.set_defaults(func=create_admin)

    seed = sub.add_parser("seed-demo", help="Create demo amenities, users and turfs")
    seed.add_argument("--force", action="store_true")
    seed.set_defaults(func=seed_demo)

    expire = sub.add_parser("expire-holds", help="Release unpaid bookings whose hold has expired")
    expire.set_defaults(func=expire_holds)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
