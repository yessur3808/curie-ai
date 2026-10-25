"""Run on Curie's server desktop: python -m scripts.x_browser_login --owner-id ID --handle HANDLE."""

import argparse
import asyncio

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--handle", required=True)
    args = parser.parse_args()
    load_dotenv()
    from connectors.x_browser import enroll_session

    asyncio.run(enroll_session(args.owner_id, args.handle.removeprefix("@")))


if __name__ == "__main__":
    main()
