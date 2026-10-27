"""Server-side account administration, for the person who runs this CareerPilot instance.

    careerpilot-admin reset-password you@example.com      # prompts for the new password
    careerpilot-admin list-users

There is no email-based reset (no mail service is configured); whoever can run commands on the server can reset
a password. Resetting signs the user out everywhere.
"""

import argparse
import getpass
import sys
from datetime import UTC, datetime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CareerPilot account administration")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("reset-password", help="set a new password for an account")
    r.add_argument("email")
    sub.add_parser("list-users", help="list accounts")
    args = parser.parse_args(argv)

    from sqlalchemy import select

    from app.core.db import get_engine, session_factory
    from app.core.security import hash_password, validate_password_strength
    from app.models import AuthSession, User

    get_engine()
    with session_factory()() as db:
        if args.command == "list-users":
            for u in db.scalars(select(User).order_by(User.id)):
                print(f"{u.id}\t{u.email}\t{u.display_name}\tcreated {u.created_at:%Y-%m-%d}")
            return 0
        user = db.scalar(select(User).where(User.email == args.email.strip().lower()))
        if user is None:
            print(f"No account with email {args.email}.", file=sys.stderr)
            return 1
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat it: "):
            print("The passwords don't match.", file=sys.stderr)
            return 1
        problem = validate_password_strength(password)
        if problem:
            print(problem, file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.failed_login_count, user.locked_until = 0, None
        now = datetime.now(UTC)
        for s in db.scalars(
            select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        ):
            s.revoked_at = now
        db.commit()
        print(f"Password updated for {user.email}. All of its sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
