import sqlite3

from dummy_build import LATEST

SCHEMA_VERSION = LATEST


class SessionDB:
    def __init__(self, db_path=None, read_only=False):
        self.c = sqlite3.connect(str(db_path or "/opt/data/state.db"))
        self.c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
        if self.c.execute("SELECT 1 FROM schema_version").fetchone():
            self.c.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        else:
            self.c.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self.c.commit()

    def close(self):
        self.c.close()
