import argparse
import sqlite3
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Repair media database by fixing mismatched IDs."
    )
    parser.add_argument("db_path", type=str, help="Path to the SQLite database file")
    return parser.parse_args()


def main():
    args = parse_args()
    db_path = Path(args.db_path)

    # Verify database file exists
    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}")
        return 1

    print(f"Opening database: {db_path}")

    # Connect to the SQLite database with type parsing and row factory
    conn = sqlite3.connect(
        db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
    )

    # Configure the connection to return rows as dictionaries
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Step 1: Fetch mismatched rows into a Python list of dictionaries
        cursor.execute(
            "SELECT incorrect.id AS incorrect_id, correct.id AS correct_id, "
            "incorrect.local_filename, incorrect.content_hash, incorrect.is_downloaded "
            "FROM media AS incorrect "
            "JOIN media AS correct "
            "ON incorrect.local_filename LIKE '%' || correct.id || '%' "
            "WHERE incorrect.local_filename NOT LIKE '%' || incorrect.id || '%'"
        )
        mismatched_rows = cursor.fetchall()

        # Convert rows to a list of dictionaries using dict(row)
        mismatched_data = [dict(row) for row in mismatched_rows]
        print(f"Found {len(mismatched_data)} mismatched rows.")

        # Step 2: Update correct rows using mismatched data
        for row in mismatched_data:
            cursor.execute(
                "UPDATE media SET local_filename = ?, content_hash = ?, "
                "is_downloaded = ? WHERE id = ?",
                (
                    row["local_filename"],
                    row["content_hash"],
                    row["is_downloaded"],
                    row["correct_id"],
                ),
            )
        print("Correct rows updated.")

        # Step 3: Delete mismatched rows using mismatched data
        for row in mismatched_data:
            cursor.execute(
                "DELETE FROM media WHERE id = ?",
                (row["incorrect_id"],),
            )
        print("Mismatched rows deleted.")

        # Commit the transaction
        conn.commit()
        print("All changes committed successfully.")

    except Exception as e:
        # Rollback in case of an error
        conn.rollback()
        print(f"An error occurred: {e}")
        return 1

    finally:
        # Close the connection
        conn.close()
        print("Database connection closed.")

    return 0


if __name__ == "__main__":
    exit(main())
