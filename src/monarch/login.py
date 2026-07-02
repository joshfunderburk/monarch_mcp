"""Interactive login to create a Monarch Money session pickle."""

import asyncio
import getpass
import os
import stat

from monarchmoney import MonarchMoney, RequireMFAException

from monarch.client import SESSION_FILE


async def _login() -> None:
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
    os.chmod(os.path.dirname(SESSION_FILE), stat.S_IRWXU)
    os.chmod(SESSION_FILE, stat.S_IRUSR | stat.S_IWUSR)
    print(f"Session saved to {SESSION_FILE}")


def main() -> None:
    asyncio.run(_login())


if __name__ == "__main__":
    main()
