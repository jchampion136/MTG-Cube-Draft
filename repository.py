import asyncio
import json
import sqlite3
from pathlib import Path

from .models import Card, UserError

SCHEMA = """
CREATE TABLE IF NOT EXISTS nominations (
 id INTEGER PRIMARY KEY,
 guild_id INTEGER NOT NULL,
 oracle_id TEXT NOT NULL,
 scryfall_id TEXT NOT NULL,
 card_name TEXT NOT NULL,
 card_json TEXT NOT NULL,
 submitted_by INTEGER NOT NULL,
 submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected','expired','banned'))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_nomination
 ON nominations(guild_id, oracle_id) WHERE status = 'pending';
CREATE TABLE IF NOT EXISTS cube_cards (
 guild_id INTEGER NOT NULL,
 oracle_id TEXT NOT NULL,
 scryfall_id TEXT NOT NULL,
 card_name TEXT NOT NULL,
 added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(guild_id, oracle_id)
);
CREATE TABLE IF NOT EXISTS banned_cards (
 guild_id INTEGER NOT NULL,
 oracle_id TEXT NOT NULL,
 card_name TEXT NOT NULL,
 reason TEXT,
 banned_by INTEGER NOT NULL,
 banned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(guild_id, oracle_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
 id INTEGER PRIMARY KEY,
 guild_id INTEGER NOT NULL,
 actor_id INTEGER NOT NULL,
 action TEXT NOT NULL,
 oracle_id TEXT NOT NULL,
 details TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
PRAGMA user_version = 1;
"""


class Repository:
    def __init__(self, path: str):
        self.path = path

    async def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        def run():
            connection = self._connection()
            try:
                with connection:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(SCHEMA)
            finally:
                connection.close()

        await asyncio.to_thread(run)

    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def _run(self, operation, write=False):
        def run():
            connection = self._connection()
            try:
                with connection:
                    if write:
                        connection.execute("BEGIN IMMEDIATE")
                    return operation(connection)
            finally:
                connection.close()

        return await asyncio.to_thread(run)

    @staticmethod
    def _check(connection, guild_id, oracle_id):
        ban = connection.execute(
            "SELECT * FROM banned_cards WHERE guild_id=? AND oracle_id=?", (guild_id, oracle_id)
        ).fetchone()
        if ban:
            reason = ban["reason"] or "No reason supplied."
            raise UserError(f"{ban['card_name']} is banned in this server. Reason: {reason}")
        accepted = connection.execute(
            "SELECT * FROM cube_cards WHERE guild_id=? AND oracle_id=?", (guild_id, oracle_id)
        ).fetchone()
        if accepted:
            raise UserError(f"{accepted['card_name']} is already in the Cube.")
        pending = connection.execute(
            "SELECT * FROM nominations WHERE guild_id=? AND oracle_id=? AND status='pending'",
            (guild_id, oracle_id),
        ).fetchone()
        if pending:
            raise UserError(
                f"{pending['card_name']} is already nominated by <@{pending['submitted_by']}>. "
                f"Use /nomination id:{pending['id']} to view it."
            )

    async def check(self, guild_id, oracle_id):
        await self._run(lambda c: self._check(c, guild_id, oracle_id))

    @staticmethod
    def _audit(c, guild_id, user_id, action, oracle_id, details):
        c.execute(
            "INSERT INTO audit_events(guild_id,actor_id,action,oracle_id,details) VALUES(?,?,?,?,?)",
            (guild_id, user_id, action, oracle_id, json.dumps(details)),
        )

    async def nominate(self, guild_id: int, user_id: int, card: Card) -> int:
        def run(c):
            self._check(c, guild_id, card.oracle_id)
            cursor = c.execute(
                "INSERT INTO nominations(guild_id,oracle_id,scryfall_id,card_name,"
                "card_json,submitted_by) VALUES(?,?,?,?,?,?)",
                (
                    guild_id,
                    card.oracle_id,
                    card.scryfall_id,
                    card.name,
                    json.dumps(card.raw),
                    user_id,
                ),
            )
            self._audit(
                c,
                guild_id,
                user_id,
                "nominate",
                card.oracle_id,
                {"nomination_id": cursor.lastrowid, "card_name": card.name},
            )
            return cursor.lastrowid

        return await self._run(run, write=True)

    async def ban(self, guild_id, user_id, card, reason):
        def run(c):
            if c.execute(
                "SELECT 1 FROM banned_cards WHERE guild_id=? AND oracle_id=?",
                (guild_id, card.oracle_id),
            ).fetchone():
                raise UserError(f"{card.name} is already banned. Use /unban-card to lift the ban.")
            c.execute(
                "INSERT INTO banned_cards(guild_id,oracle_id,card_name,reason,banned_by) "
                "VALUES(?,?,?,?,?)",
                (guild_id, card.oracle_id, card.name, reason, user_id),
            )
            closed = c.execute(
                "UPDATE nominations SET status='banned' WHERE guild_id=? "
                "AND oracle_id=? AND status='pending'",
                (guild_id, card.oracle_id),
            ).rowcount
            in_cube = bool(
                c.execute(
                    "SELECT 1 FROM cube_cards WHERE guild_id=? AND oracle_id=?",
                    (guild_id, card.oracle_id),
                ).fetchone()
            )
            self._audit(
                c,
                guild_id,
                user_id,
                "ban",
                card.oracle_id,
                {"card_name": card.name, "reason": reason, "closed": closed},
            )
            return closed, in_cube

        return await self._run(run, write=True)

    async def unban(self, guild_id, user_id, card):
        def run(c):
            changed = c.execute(
                "DELETE FROM banned_cards WHERE guild_id=? AND oracle_id=?",
                (guild_id, card.oracle_id),
            ).rowcount
            if not changed:
                raise UserError(f"{card.name} is not banned in this server.")
            self._audit(c, guild_id, user_id, "unban", card.oracle_id, {"card_name": card.name})

        await self._run(run, write=True)

    async def list_rows(self, table, guild_id, page):
        if table not in {"banned_cards", "nominations"}:
            raise ValueError("Unsupported table")
        where = "guild_id=?" + (" AND status='pending'" if table == "nominations" else "")

        def run(c):
            total = c.execute(
                f"SELECT count(*) FROM {table} WHERE {where}", (guild_id,)
            ).fetchone()[0]
            rows = c.execute(
                f"SELECT * FROM {table} WHERE {where} ORDER BY card_name COLLATE NOCASE "
                "LIMIT 10 OFFSET ?",
                (guild_id, (page - 1) * 10),
            ).fetchall()
            return total, [dict(row) for row in rows]

        return await self._run(run)

    async def nomination(self, guild_id, nomination_id):
        def run(c):
            row = c.execute(
                "SELECT * FROM nominations WHERE guild_id=? AND id=?", (guild_id, nomination_id)
            ).fetchone()
            if not row:
                raise UserError("That nomination wasn't found in this server.")
            return dict(row)

        return await self._run(run)
