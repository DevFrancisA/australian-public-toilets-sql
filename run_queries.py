"""
run_queries.py -- Execute every query in queries.sql and print the results.

Each statement is printed as a labelled, aligned table. A statement that fails
is reported and skipped, so one broken query never stops the run. The exit code
is 1 if any query failed, which makes the script usable as a CI smoke test.

Usage
-----
    python run_queries.py [queries.sql] [toilets.db] [--max-rows N]
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys

DEFAULT_SQL = "queries.sql"
DEFAULT_DB = "toilets.db"
DEFAULT_MAX_ROWS = 25
LINE_WIDTH = 100

# "-- QUERY 4: Pay-toilet hotspots"
LABEL_RE = re.compile(r"^\s*--\s*QUERY\s*(\d+)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def use_utf8_stdout() -> None:
    """Windows consoles default to cp1252 and choke on town names with accents."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - older/odd streams
        pass


def split_statements(sql_text: str) -> list[tuple[str, str]]:
    """
    Split the file into (label, statement) pairs.

    Splitting on a bare ';' is not safe here: the explanatory comments contain
    semicolons, and so could a string literal. This walks the text instead,
    skipping over '--' comments and quoted strings, and only breaks on a
    semicolon that is really at statement level.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    index, end = 0, len(sql_text)

    while index < end:
        char = sql_text[index]

        if char == "-" and sql_text.startswith("--", index):
            newline = sql_text.find("\n", index)
            newline = end if newline == -1 else newline + 1
            buffer.append(sql_text[index:newline])
            index = newline
            continue

        if char in ("'", '"'):
            quote = char
            cursor = index + 1
            while cursor < end:
                if sql_text[cursor] == quote:
                    if sql_text.startswith(quote * 2, cursor):  # escaped quote
                        cursor += 2
                        continue
                    cursor += 1
                    break
                cursor += 1
            buffer.append(sql_text[index:cursor])
            index = cursor
            continue

        if char == ";":
            chunks.append("".join(buffer))
            buffer = []
            index += 1
            continue

        buffer.append(char)
        index += 1

    chunks.append("".join(buffer))

    statements = []
    for chunk in chunks:
        # A chunk only counts if something survives stripping the comments.
        body = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if not body:
            continue
        labels = LABEL_RE.findall(chunk)
        label = f"QUERY {labels[-1][0]}: {labels[-1][1]}" if labels else "UNLABELLED QUERY"
        statements.append((label, chunk.strip()))
    return statements


def render(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def print_table(headers: list[str], rows: list[tuple], max_rows: int) -> None:
    if not rows:
        print("  (no rows)")
        return

    shown = rows[:max_rows]
    cells = [[render(v) for v in row] for row in shown]
    widths = [
        max(len(header), *(len(row[i]) for row in cells)) if cells else len(header)
        for i, header in enumerate(headers)
    ]
    # Right-align anything that is entirely numeric.
    numeric = [
        all(re.fullmatch(r"-?[\d,]*\.?\d*", row[i] or "") and row[i] for row in cells)
        for i in range(len(headers))
    ]

    def fmt(values: list[str]) -> str:
        return "  ".join(
            v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i])
            for i, v in enumerate(values)
        )

    print("  " + fmt(headers))
    print("  " + "  ".join("-" * w for w in widths))
    for row in cells:
        print("  " + fmt(row))
    if len(rows) > max_rows:
        print(f"  ... {len(rows) - max_rows:,} more row(s) not shown")
    print(f"  [{len(rows):,} row(s)]")


def main() -> int:
    use_utf8_stdout()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sql_path = args[0] if args else DEFAULT_SQL
    db_path = args[1] if len(args) > 1 else DEFAULT_DB

    max_rows = DEFAULT_MAX_ROWS
    for i, arg in enumerate(sys.argv):
        if arg == "--max-rows" and i + 1 < len(sys.argv):
            max_rows = int(sys.argv[i + 1])

    if not os.path.exists(db_path):
        print(f"Database {db_path!r} not found. Run: python load_data.py")
        return 1
    if not os.path.exists(sql_path):
        print(f"SQL file {sql_path!r} not found.")
        return 1

    with open(sql_path, encoding="utf-8") as handle:
        statements = split_statements(handle.read())

    print("=" * LINE_WIDTH)
    print(f"National Public Toilet Map -- {len(statements)} queries from {sql_path}")
    print("=" * LINE_WIDTH)

    succeeded, failed = 0, []
    with sqlite3.connect(db_path) as conn:
        for label, statement in statements:
            print(f"\n{'-' * LINE_WIDTH}\n{label}\n{'-' * LINE_WIDTH}")
            try:
                cursor = conn.execute(statement)
                rows = cursor.fetchall()
                headers = [d[0] for d in cursor.description] if cursor.description else []
            except sqlite3.Error as exc:
                # Skip and keep going: one bad query must not halt the run.
                print(f"  SKIPPED -- {exc.__class__.__name__}: {exc}")
                failed.append((label, str(exc)))
                continue
            print_table(headers, rows, max_rows)
            succeeded += 1

    print("\n" + "=" * LINE_WIDTH)
    print(f"Ran {succeeded}/{len(statements)} queries successfully.")
    if failed:
        print(f"{len(failed)} failed:")
        for label, error in failed:
            print(f"  - {label}: {error}")
    print("=" * LINE_WIDTH)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
