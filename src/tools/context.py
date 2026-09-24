"""External-system access for tools, kept behind small protocols so
mutating-tool logic (idempotency, compensation) is unit-testable without a
live Postgres, while a real Postgres-backed implementation exists for use
against docker-compose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import asyncpg
import httpx


class ExecutedActionsStore(Protocol):
    """The `executed_actions` idempotency ledger (see db/init.sql)."""

    async def get(self, idempotency_key: str) -> dict | None: ...

    async def record(
        self, idempotency_key: str, tool_name: str, args: dict, result: dict, compensation: dict
    ) -> None: ...


class InMemoryExecutedActionsStore:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    async def get(self, idempotency_key: str) -> dict | None:
        return self._rows.get(idempotency_key)

    async def record(
        self, idempotency_key: str, tool_name: str, args: dict, result: dict, compensation: dict
    ) -> None:
        self._rows.setdefault(
            idempotency_key,
            {"tool_name": tool_name, "args": args, "result": result, "compensation": compensation},
        )


class PostgresExecutedActionsStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def get(self, idempotency_key: str) -> dict | None:
        conn = await asyncpg.connect(self._dsn)
        try:
            row = await conn.fetchrow(
                "SELECT tool_name, args, result, compensation FROM executed_actions WHERE idempotency_key = $1",
                idempotency_key,
            )
        finally:
            await conn.close()
        if row is None:
            return None
        return dict(row)

    async def record(
        self, idempotency_key: str, tool_name: str, args: dict, result: dict, compensation: dict
    ) -> None:
        import json

        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(
                """
                INSERT INTO executed_actions (idempotency_key, tool_name, args, result, compensation)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                idempotency_key,
                tool_name,
                json.dumps(args),
                json.dumps(result),
                json.dumps(compensation),
            )
        finally:
            await conn.close()


class RecordsRepository(Protocol):
    """The mock production `records` table."""

    async def get_by_kind(self, kind: str | None, limit: int) -> list[dict]: ...

    async def get_by_ids(self, ids: list[int]) -> list[dict]: ...

    async def delete(self, ids: list[int]) -> None: ...

    async def insert_many(self, rows: list[dict]) -> None: ...


class InMemoryRecordsRepository:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._next_id = (max((r["id"] for r in rows), default=0) + 1) if rows else 1
        self._rows: dict[int, dict] = {r["id"]: dict(r) for r in (rows or [])}

    async def get_by_kind(self, kind: str | None, limit: int) -> list[dict]:
        rows = list(self._rows.values())
        if kind is not None:
            rows = [r for r in rows if r["kind"] == kind]
        return sorted(rows, key=lambda r: r["id"])[:limit]

    async def get_by_ids(self, ids: list[int]) -> list[dict]:
        return [dict(self._rows[i]) for i in ids if i in self._rows]

    async def delete(self, ids: list[int]) -> None:
        for i in ids:
            self._rows.pop(i, None)

    async def insert_many(self, rows: list[dict]) -> None:
        for row in rows:
            row = dict(row)
            row.setdefault("id", self._next_id)
            self._next_id = max(self._next_id, row["id"] + 1)
            self._rows[row["id"]] = row


class PostgresRecordsRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def get_by_kind(self, kind: str | None, limit: int) -> list[dict]:
        conn = await asyncpg.connect(self._dsn)
        try:
            if kind is None:
                rows = await conn.fetch(
                    "SELECT id, kind, payload FROM records ORDER BY id LIMIT $1", limit
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, kind, payload FROM records WHERE kind = $1 ORDER BY id LIMIT $2",
                    kind,
                    limit,
                )
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    async def get_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        conn = await asyncpg.connect(self._dsn)
        try:
            rows = await conn.fetch(
                "SELECT id, kind, payload FROM records WHERE id = ANY($1::int[]) ORDER BY id", ids
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            return
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute("DELETE FROM records WHERE id = ANY($1::int[])", ids)
        finally:
            await conn.close()

    async def insert_many(self, rows: list[dict]) -> None:
        if not rows:
            return
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.executemany(
                "INSERT INTO records (id, kind, payload) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
                [(r["id"], r["kind"], r["payload"]) for r in rows],
            )
        finally:
            await conn.close()


@dataclass
class ToolContext:
    """Everything a tool needs to reach the sandbox environment."""

    clients: dict[str, httpx.AsyncClient]
    store: ExecutedActionsStore = field(default_factory=InMemoryExecutedActionsStore)
    records: RecordsRepository = field(default_factory=InMemoryRecordsRepository)

    def client(self, service: str) -> httpx.AsyncClient:
        try:
            return self.clients[service]
        except KeyError as exc:
            raise ValueError(f"unknown service {service!r}; known: {sorted(self.clients)}") from exc
