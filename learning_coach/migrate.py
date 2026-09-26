"""Show or apply schema migrations:  python -m learning_coach.migrate [--status]
Migrations also run automatically when the server starts (they are idempotent)."""
from __future__ import annotations

import os
import sys

from .store import MIGRATIONS, Store


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    url = os.getenv("DATABASE_URL") or os.getenv("PROGRESS_DB", "progress.db")
    st = Store(url, migrate=False)
    st._x("create table if not exists schema_migrations (version integer primary key, applied double precision not null)")
    before = st.schema_version()
    if "--status" in argv:
        print(f"current version {before}, latest {MIGRATIONS[-1][0]}")
        return 0
    applied = st.migrate()
    print(f"was {before}; applied {applied or 'nothing'}; now {st.schema_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
