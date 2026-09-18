import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quoted_identifier(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return f'"{value}"'


def ensure_database_schema():
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    pull_request_columns = {column["name"] for column in inspector.get_columns("pull_requests")}
    commit_columns = {column["name"] for column in inspector.get_columns("commits")}

    pull_request_alterations = {
        "commit_count": "INTEGER NOT NULL DEFAULT 0",
        "changed_files": "INTEGER NOT NULL DEFAULT 0",
        "additions": "INTEGER NOT NULL DEFAULT 0",
        "deletions": "INTEGER NOT NULL DEFAULT 0",
        "review_count": "INTEGER NOT NULL DEFAULT 0",
        "reviewers_count": "INTEGER NOT NULL DEFAULT 0",
        "approvals_count": "INTEGER NOT NULL DEFAULT 0",
        "requested_changes_count": "INTEGER NOT NULL DEFAULT 0",
        "review_decision": "VARCHAR(64)",
        "first_review_comment_at": "DATETIME",
        "last_review_comment_at": "DATETIME",
    }
    commit_alterations = {
        "committed_at": "DATETIME",
    }

    with engine.begin() as connection:
        for column_name, column_definition in pull_request_alterations.items():
            if column_name not in pull_request_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE {_quoted_identifier('pull_requests')} "
                        f"ADD COLUMN {_quoted_identifier(column_name)} {column_definition}"
                    )
                )

        for column_name, column_definition in commit_alterations.items():
            if column_name not in commit_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE {_quoted_identifier('commits')} "
                        f"ADD COLUMN {_quoted_identifier(column_name)} {column_definition}"
                    )
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
