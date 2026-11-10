import json
import sqlite3
from contextlib import closing
from pathlib import Path


class State:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.path = directory / "signalpoint.sqlite3"
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS installs "
                "(game TEXT, mod TEXT, body TEXT, PRIMARY KEY(game,mod))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS commits (id TEXT PRIMARY KEY)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def game_key(game):
        return str(game.resolve()).casefold()

    def get(self, key, default=None):
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def installs(self, game):
        with closing(self.connect()) as db:
            return {
                row[0]: json.loads(row[1])
                for row in db.execute(
                    "SELECT mod,body FROM installs WHERE game=?", (self.game_key(game),)
                )
            }

    def commit(self, game, mod, receipt, transaction):
        with closing(self.connect()) as db, db:
            if receipt is None:
                db.execute(
                    "DELETE FROM installs WHERE game=? AND mod=?", (self.game_key(game), mod)
                )
            else:
                db.execute(
                    "INSERT OR REPLACE INTO installs VALUES (?,?,?)",
                    (self.game_key(game), mod, json.dumps(receipt)),
                )
            db.execute("INSERT INTO commits VALUES (?)", (transaction,))

    def committed(self, transaction):
        with closing(self.connect()) as db:
            return (
                db.execute("SELECT 1 FROM commits WHERE id=?", (transaction,)).fetchone()
                is not None
            )

    def forget_commit(self, transaction):
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM commits WHERE id=?", (transaction,))
