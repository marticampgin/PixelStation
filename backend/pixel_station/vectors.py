"""sqlite-vec adapter with a portable fallback; extension details stay in this module."""

import json
import math
import sqlite3
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return (
        sum(x * y for x, y in zip(left, right, strict=True)) / denominator if denominator else 0.0
    )


class SqliteVectorStore:
    def __init__(self, session: Session, model_class):
        self.session = session
        self.model_class = model_class
        self.backend = "portable"

    def add(self, key: str, vector: list[float]) -> None:
        if not vector or any(not math.isfinite(value) for value in vector):
            raise ValueError("Embedding must contain finite numbers")
        row = self.session.get(self.model_class, key)
        if row:
            row.embedding = vector

    def update(self, key: str, vector: list[float]) -> None:
        self.add(key, vector)

    def delete(self, key: str) -> None:
        row = self.session.get(self.model_class, key)
        if row:
            row.embedding = None

    def search(self, vector: list[float], limit: int = 4) -> list[tuple[str, float]]:
        if not vector or not any(vector) or any(not math.isfinite(value) for value in vector):
            return []
        connection = cast(
            sqlite3.Connection, self.session.connection().connection.driver_connection
        )
        try:
            import sqlite_vec

            try:
                connection.execute("SELECT vec_version()")
            except sqlite3.OperationalError:
                connection.enable_load_extension(True)
                try:
                    sqlite_vec.load(connection)
                finally:
                    connection.enable_load_extension(False)
            # The identifier originates from trusted ORM metadata, never from user/model input.
            table = self.model_class.__table__.name.replace('"', '""')
            rows = connection.execute(
                f'SELECT id, 1 - vec_distance_cosine(vec_f32(embedding), vec_f32(?)) AS similarity FROM "{table}" WHERE json_array_length(embedding) = ? ORDER BY similarity DESC LIMIT ?',
                (json.dumps(vector), len(vector), max(1, min(limit, 100))),
            ).fetchall()
            self.backend = "sqlite-vec"
            return [(identity, float(score)) for identity, score in rows if score is not None]
        except (ImportError, AttributeError, sqlite3.Error):
            fallback_rows = self.session.scalars(
                select(self.model_class).where(self.model_class.embedding.is_not(None))
            )
            matches = [
                (row.id, cosine(vector, row.embedding))
                for row in fallback_rows
                if isinstance(row.embedding, list) and len(row.embedding) == len(vector)
            ]
            return sorted(matches, key=lambda pair: pair[1], reverse=True)[:limit]
