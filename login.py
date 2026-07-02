"""One-time interactive login to create a Monarch Money session pickle."""

import asyncio
import getpass

from monarchmoney import MonarchMoney, RequireMFAException

SESSION_FILE = ".mm/mm_session.pickle"


async def main() -> None:
    mm = MonarchMoney(session_file=SESSION_FILE)
    email = input("Email: ")
    password = getpass.getpass("Password: ")

    try:
        await mm.login(email, password)
    except RequireMFAException:
        code = input("MFA code: ")
        await mm.multi_factor_authenticate(email, password, code)

    mm.save_session()
    print(f"Session saved to {SESSION_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
