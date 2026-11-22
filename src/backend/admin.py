"""
    Operator commands. Run from the repository root (or inside the container):

        PYTHONPATH=src python -m backend.admin gen-key
        PYTHONPATH=src python -m backend.admin create-account --label "me"
        PYTHONPATH=src python -m backend.admin rotate-token 1
        PYTHONPATH=src python -m backend.admin list-accounts

    Apply migrations first: alembic upgrade head.
"""

import argparse
import sys

from sqlalchemy import select

from . import accounts, crypto
from .config import Settings
from .db import Database
from .models import Account


def main(argv=None, database=None, out=sys.stdout):
    parser = argparse.ArgumentParser(prog="python -m backend.admin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gen-key", help="print a new AUTOAPPLIER_DATA_KEY")
    create = sub.add_parser("create-account", help="create an account and print its API token")
    create.add_argument("--label", default="")
    create.add_argument("--daily-llm-cap", type=int, default=20)
    rotate = sub.add_parser("rotate-token", help="replace an account's API token")
    rotate.add_argument("account_id", type=int)
    sub.add_parser("list-accounts")
    cap = sub.add_parser("set-cap", help="set an account's daily limit of tailored resumes")
    cap.add_argument("account_id", type=int)
    cap.add_argument("cap", type=int)
    args = parser.parse_args(argv)

    if args.command == "gen-key":
        print(crypto.generate_key(), file=out)
        return 0

    database = database or Database(Settings().database_url)
    if not database.configured:
        print("AUTOAPPLIER_DATABASE_URL is not set.", file=sys.stderr)
        return 2

    with database.session() as session:
        if args.command == "create-account":
            account, token = accounts.create_account(session, label=args.label,
                                                     daily_llm_cap=args.daily_llm_cap)
            print(f"Created account {account.id}. API token (shown once; paste it into the "
                  f"extension's settings):\n{token}", file=out)
        elif args.command == "rotate-token":
            token = accounts.rotate_token(session, args.account_id)
            print(f"New API token for account {args.account_id} (the old one no longer works):"
                  f"\n{token}", file=out)
        elif args.command == "list-accounts":
            for account in session.scalars(select(Account).order_by(Account.id)):
                flags = " (disabled)" if account.disabled else ""
                auto = " automation=on" if account.automation_enabled else ""
                print(f"{account.id}\t{account.label}\tcap={account.daily_llm_cap}{auto}{flags}", file=out)
        elif args.command == "set-cap":
            account = session.get(Account, args.account_id)
            if account is None:
                print(f"No account {args.account_id}.", file=sys.stderr)
                return 1
            account.daily_llm_cap = max(0, args.cap)
            print(f"Account {account.id} may tailor {account.daily_llm_cap} resumes per day.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
