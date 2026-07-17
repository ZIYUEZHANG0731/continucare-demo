from __future__ import annotations

import sqlite3

from continucare.db import initialize_database, reset_demo


def test_database_initializes_and_marks_synthetic_only(tmp_path):
    db_path = tmp_path / "demo.db"

    initialize_database(db_path)

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM demo_metadata WHERE key = 'data_classification'"
        ).fetchone()
    assert row == ("synthetic_only",)


def test_demo_reset_recreates_clean_database(tmp_path):
    db_path = tmp_path / "demo.db"
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO demo_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            ("temporary", "remove-me", "2026-01-01T00:00:00+00:00"),
        )

    reset_demo(db_path)

    with sqlite3.connect(db_path) as connection:
        temporary = connection.execute(
            "SELECT value FROM demo_metadata WHERE key = 'temporary'"
        ).fetchone()
        classification = connection.execute(
            "SELECT value FROM demo_metadata WHERE key = 'data_classification'"
        ).fetchone()
    assert temporary is None
    assert classification == ("synthetic_only",)
