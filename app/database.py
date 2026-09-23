import re

from sqlalchemy import DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
COLUMN_DEFINITION_PATTERN = re.compile(r"^[A-Z0-9(), ]+$")


def _quoted_identifier(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return f'"{value}"'


def _safe_column_definition(value: str) -> str:
    if not COLUMN_DEFINITION_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe SQL column definition: {value}")
    return value


def _compile_column_definition(column_type, *, nullable: bool = True, default: str | None = None) -> str:
    parts = [column_type.compile(dialect=engine.dialect).upper()]
    if not nullable:
        parts.append("NOT NULL")
    if default is not None:
        parts.append(f"DEFAULT {default}")
    return _safe_column_definition(" ".join(parts))


def _apply_column_alterations(connection, table_name: str, existing_columns: set[str], alterations: dict[str, str]) -> None:
    for column_name, column_definition in alterations.items():
        if column_name not in existing_columns:
            connection.execute(
                text(
                    f"ALTER TABLE {_quoted_identifier(table_name)} "
                    f"ADD COLUMN {_quoted_identifier(column_name)} {_safe_column_definition(column_definition)}"
                )
            )


def ensure_database_schema():
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    pull_request_columns = {column["name"] for column in inspector.get_columns("pull_requests")}
    commit_columns = {column["name"] for column in inspector.get_columns("commits")}
    pull_request_comment_columns = {
        column["name"] for column in inspector.get_columns("pull_request_comments")
    }

    pull_request_alterations = {
        "commit_count": _compile_column_definition(Integer(), nullable=False, default="0"),
        "changed_files": _compile_column_definition(Integer(), nullable=False, default="0"),
        "additions": _compile_column_definition(Integer(), nullable=False, default="0"),
        "deletions": _compile_column_definition(Integer(), nullable=False, default="0"),
        "review_count": _compile_column_definition(Integer(), nullable=False, default="0"),
        "reviewers_count": _compile_column_definition(Integer(), nullable=False, default="0"),
        "approvals_count": _compile_column_definition(Integer(), nullable=False, default="0"),
        "requested_changes_count": _compile_column_definition(Integer(), nullable=False, default="0"),
        "review_decision": _compile_column_definition(String(64)),
        "first_review_comment_at": _compile_column_definition(DateTime()),
        "last_review_comment_at": _compile_column_definition(DateTime()),
    }
    commit_alterations = {
        "committed_at": _compile_column_definition(DateTime()),
    }
    pull_request_comment_alterations: dict[str, str] = {}

    with engine.begin() as connection:
        _apply_column_alterations(connection, "pull_requests", pull_request_columns, pull_request_alterations)
        _apply_column_alterations(connection, "commits", commit_columns, commit_alterations)
        _apply_column_alterations(
            connection,
            "pull_request_comments",
            pull_request_comment_columns,
            pull_request_comment_alterations,
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
