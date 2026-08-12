"""
Generate a password hash for `api.users`, or add an account outright.

    # print a hash to paste into config.json
    python -m backend.tools.passwd 'my password'

    # or write the account straight into the config
    python -m backend.tools.passwd 'my password' --user vijay --config config/config.json

Reading the password from argv is fine here and awkward to avoid: this is run by
the operator on their own box. Pass --stdin to keep it out of shell history.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from ..api.auth import hash_password


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="backend.tools.passwd")
    ap.add_argument("password", nargs="?", help="the password (omit with --stdin)")
    ap.add_argument("--stdin", action="store_true",
                    help="read the password from stdin instead of argv")
    ap.add_argument("--user", help="username; with --config, adds or updates them")
    ap.add_argument("--config", help="path to config.json to update in place")
    args = ap.parse_args(argv)

    password = sys.stdin.readline().rstrip("\n") if args.stdin else args.password
    if not password:
        ap.error("a password is required (positional, or --stdin)")

    digest = hash_password(password)

    if not args.config:
        if args.user:
            print(json.dumps({"username": args.user, "password_hash": digest}, indent=2))
        else:
            print(digest)
        return 0

    if not args.user:
        ap.error("--config also needs --user")

    path = pathlib.Path(args.config)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    api = cfg.setdefault("api", {})
    users = api.setdefault("users", [])

    for u in users:
        if str(u.get("username", "")).strip().lower() == args.user.strip().lower():
            u["password_hash"] = digest
            action = "updated"
            break
    else:
        users.append({"username": args.user, "password_hash": digest})
        action = "added"

    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"{action} user {args.user!r} in {path}")
    print(f"accounts now: {[u['username'] for u in users]}")
    print("restart the service for it to take effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
