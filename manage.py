"""Small command-line helper.

    python manage.py init-db                      create the empty database
    python manage.py create-warden EMAIL          create a warden login (asks for a password)
"""
import getpass
import os
import sys
from pathlib import Path

import hostel
from app import create_app


def main(argv):
    app = create_app()
    db_path = app.config["DATABASE"]
    if len(argv) >= 2 and argv[1] == "init-db":
        print(f"Database ready at {db_path}")
    elif len(argv) == 3 and argv[1] == "create-warden":
        password = os.environ.get("HOSTEL_WARDEN_PASSWORD") or getpass.getpass("Password (min 8 characters): ")
        db = hostel.connect(db_path)
        try:
            hostel.create_user(db, argv[2], password, "warden")
        except hostel.HostelError as exc:
            sys.exit(f"Error: {exc}")
        print(f"Warden {argv[2]} created.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
