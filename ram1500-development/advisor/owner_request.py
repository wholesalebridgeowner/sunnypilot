"""Queue one owner settings request from the authenticated private Railway project.

Run in the service's Python environment (boto3 as pinned in Dockerfile):
    railway run python owner_request.py KEY=VALUE [KEY=VALUE ...]

The operator needs the service store and notification credentials. The PC
does not contact the truck. The owner approves all displayed values together.
"""
import argparse
import sys

from owner_settings import parse_owner_value, validate_owner_change
from service_app import service_from_env
from settings_guard import GuardError


def parse_change(assignments):
    change = {}
    for assignment in assignments:
        key, sep, raw = assignment.partition("=")
        if not sep or not key or not raw:
            raise GuardError("Each setting must be written KEY=VALUE.")
        if key in change:
            raise GuardError(f"Duplicate setting: {key}")
        change[key] = parse_owner_value(key, raw)
    validate_owner_change(change)
    return change


def main(argv=None, *, connect=service_from_env):
    parser = argparse.ArgumentParser(description="Send one approval button for these exact settings.")
    parser.add_argument("settings", metavar="KEY=VALUE", nargs="+")
    args = parser.parse_args(argv)
    try:
        change = parse_change(args.settings)
        sid, sent = connect().request_owner_change(change)
    except GuardError as error:
        parser.error(str(error))
    except KeyError:
        print("Missing service configuration. Run in the authenticated advisor project environment.", file=sys.stderr)
        return 1
    except ModuleNotFoundError:
        print("The advisor service Python dependencies are missing. Use the boto3 version in Dockerfile.",
              file=sys.stderr)
        return 1
    except Exception as error:
        # External exception strings can include service endpoints or tokens.
        print(f"Could not confirm the request ({type(error).__name__}).", file=sys.stderr)
        return 1
    if not sent:
        print(f"Request {sid} is pending. Notification delivery was unconfirmed. "
              "Only an approval tap can release it; the button expires in 12 hours.", file=sys.stderr)
        return 1
    print(f"Request {sid} is pending. One approval button was sent for all requested values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
