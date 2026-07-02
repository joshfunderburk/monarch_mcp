"""One-time interactive login to create a Monarch Money session pickle."""

import asyncio
import getpass
import os

from monarchmoney import MonarchMoney, RequireMFAException

DEFAULT_SESSION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".mm", "mm_session.pickle"
)
SESSION_FILE = os.environ.get("MONARCH_SESSION_FILE", DEFAULT_SESSION_FILE)


async def main() -> None:
    mm = MonarchMoney(session_file=SESSION_FILE)
    email = input("Email: ")
    password = getpass.getpass("Password: ")

    try:
        # use_saved_session=False forces a fresh login even if a (possibly
        # expired) session pickle already exists.
        await mm.login(email, password, use_saved_session=False)
    except RequireMFAException:
        code = input("MFA code: ")
        await mm.multi_factor_authenticate(email, password, code)

    mm.save_session()
    print(f"Session saved to {SESSION_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
