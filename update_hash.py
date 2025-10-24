
import argparse
import os
import sqlite3
from dotenv import load_dotenv


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            url TEXT PRIMARY KEY,
            hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def upsert_hash(db_path: str, url: str, new_hash: str) -> None:
    conn = sqlite3.connect(db_path)
    ensure_table(conn)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO snapshots(url, hash, updated_at) VALUES (?,?,datetime('now'))\n"
        "ON CONFLICT(url) DO UPDATE SET hash=excluded.hash, updated_at=datetime('now')",
        (url, new_hash),
    )
    conn.commit()
    conn.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Force a different snapshot hash for testing")
    parser.add_argument("url", help="Target URL to set a dummy hash for")
    parser.add_argument("hash", nargs="?", default="deadbeef", help="Hash value to set (default: deadbeef)")
    parser.add_argument("--db", dest="db", default=None, help="Path to SQLite DB (overrides SQLITE_PATH)")
    args = parser.parse_args()

    db_path = args.db or os.getenv("SQLITE_PATH", "./watchdog.db")
    upsert_hash(db_path, args.url, args.hash)
    print(f"Set snapshot hash for {args.url} -> {args.hash} in {db_path}")


if __name__ == "__main__":
    main()
