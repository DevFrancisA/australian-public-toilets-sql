"""
load_data.py — Load the National Public Toilet Map CSV into a SQLite database.

What it does
------------
1. Reads the CSV with an encoding fallback (utf-8, then latin-1).
2. Normalises column names (strips spaces, slashes, punctuation).
3. Coerces Latitude/Longitude to REAL, dropping rows without usable coordinates
   only from the coordinate columns (the rows themselves are kept).
4. Auto-detects every text column whose values are only 'true'/'false'
   (any casing) and rewrites it as an INTEGER 0/1 column.
5. Writes everything to `toilets.db`, table `toilets`, and prints a summary.

Usage
-----
    python load_data.py [path/to/export.csv] [path/to/toilets.db]
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys

import pandas as pd

DEFAULT_DB = "toilets.db"
TABLE = "toilets"
ENCODINGS = ("utf-8", "latin-1")

# Values that count as booleans, lower-cased.
TRUE_VALUES = {"true", "t", "yes", "y", "1"}
FALSE_VALUES = {"false", "f", "no", "n", "0"}


def find_csv() -> str:
    """Return the CSV to load: the newest toiletmapexport*.csv in the cwd."""
    matches = sorted(glob.glob("toiletmapexport*.csv")) or sorted(glob.glob("*.csv"))
    if not matches:
        sys.exit("No CSV found. Pass the path explicitly: python load_data.py <file.csv>")
    return matches[-1]


def read_csv_with_fallback(path: str) -> tuple[pd.DataFrame, str]:
    """Read `path` as all-text, trying each encoding in turn."""
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            df = pd.read_csv(path, dtype=str, encoding=encoding, keep_default_na=True)
            return df, encoding
        except UnicodeDecodeError as exc:  # try the next encoding
            last_error = exc
            print(f"  ! {encoding} failed ({exc.__class__.__name__}), trying next encoding")
    raise RuntimeError(f"Could not decode {path} with any of {ENCODINGS}") from last_error


def normalise_column(name: str) -> str:
    """'Parking / Accessible (note)' -> 'Parking_Accessible_note'."""
    clean = name.strip()
    clean = re.sub(r"[\s/\\-]+", "_", clean)      # whitespace and slashes -> underscore
    clean = re.sub(r"[^0-9A-Za-z_]", "", clean)    # drop brackets, dots, commas, ...
    clean = re.sub(r"_+", "_", clean).strip("_")   # collapse runs of underscores
    if not clean:
        clean = "unnamed"
    if clean[0].isdigit():
        clean = f"c_{clean}"
    return clean


def normalise_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """Rename every column, keeping names unique. Returns the renames that changed."""
    seen: dict[str, int] = {}
    new_names, changed = [], []
    for original in df.columns:
        clean = normalise_column(str(original))
        if clean in seen:
            seen[clean] += 1
            clean = f"{clean}_{seen[clean]}"
        else:
            seen[clean] = 0
        new_names.append(clean)
        if clean != original:
            changed.append((str(original), clean))
    df.columns = new_names
    return df, changed


def is_text_column(series: pd.Series) -> bool:
    """True for object/str-backed columns (pandas >= 3 uses a dedicated str dtype)."""
    return not pd.api.types.is_numeric_dtype(series) and pd.api.types.is_string_dtype(series)


def is_boolean_column(series: pd.Series) -> bool:
    """True if every non-null value is a recognised true/false token."""
    values = {str(v).strip().lower() for v in series.dropna().unique()}
    if not values:
        return False
    return values <= (TRUE_VALUES | FALSE_VALUES)


def to_int_bool(series: pd.Series) -> pd.Series:
    """'True'/'false'/None -> 1/0/NULL as a nullable integer."""
    lowered = series.astype("string").str.strip().str.lower()
    mapped = lowered.map(lambda v: 1 if v in TRUE_VALUES else (0 if v in FALSE_VALUES else None))
    return pd.to_numeric(mapped, errors="coerce").astype("Int64")


def coerce_coordinates(df: pd.DataFrame) -> list[str]:
    """Convert any Latitude/Longitude columns to float. Returns the columns touched."""
    touched = []
    for column in df.columns:
        if column.lower() in {"latitude", "longitude", "lat", "lon", "lng", "long"}:
            df[column] = pd.to_numeric(df[column], errors="coerce")
            touched.append(column)
    return touched


def load(csv_path: str, db_path: str) -> None:
    print(f"Reading {csv_path} ({os.path.getsize(csv_path) / 1e6:.1f} MB)")
    df, encoding = read_csv_with_fallback(csv_path)
    print(f"  decoded as {encoding}: {len(df):,} rows x {len(df.columns)} columns")

    df, renamed = normalise_columns(df)
    if renamed:
        print(f"  normalised {len(renamed)} column name(s):")
        for original, clean in renamed:
            print(f"      {original!r} -> {clean}")
    else:
        print("  column names were already clean")

    coordinate_columns = coerce_coordinates(df)
    for column in coordinate_columns:
        bad = int(df[column].isna().sum())
        print(f"  {column}: coerced to REAL ({bad:,} unparseable -> NULL)")

    boolean_columns = [
        c for c in df.columns
        if c not in coordinate_columns and is_text_column(df[c]) and is_boolean_column(df[c])
    ]
    for column in boolean_columns:
        df[column] = to_int_bool(df[column])
    print(f"  converted {len(boolean_columns)} true/false column(s) to INTEGER 0/1")

    # Trim stray whitespace out of the remaining text columns.
    for column in df.columns:
        if is_text_column(df[column]):
            df[column] = df[column].astype("string").str.strip().replace({"": None})

    if os.path.exists(db_path):
        os.remove(db_path)

    with sqlite3.connect(db_path) as conn:
        df.to_sql(TABLE, conn, if_exists="replace", index=False)
        # Indexes on the columns the analytical queries filter and group by.
        for column in ("State", "Town", "FacilityType"):
            if column in df.columns:
                conn.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_{column.lower()} "
                    f"ON {TABLE}({column})"
                )
        conn.commit()

        row_count = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        schema = conn.execute(f"PRAGMA table_info({TABLE})").fetchall()

    print(f"\nWrote {db_path} -> table '{TABLE}'")
    print(f"Rows: {row_count:,}")
    print(f"Columns ({len(schema)}):")
    for _, name, sql_type, *_rest in schema:
        flag = "  [bool 0/1]" if name in boolean_columns else ""
        print(f"    {name:<22} {sql_type}{flag}")


if __name__ == "__main__":
    csv_arg = sys.argv[1] if len(sys.argv) > 1 else find_csv()
    db_arg = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_DB
    load(csv_arg, db_arg)
