import logging
import re
import time

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class DatabaseUnavailableError(RuntimeError):
    """The database could not be reached within the startup timeout."""


def wait_for_database(timeout_seconds: float | None = None, interval_seconds: float = 2.0) -> None:
    """Block until the database accepts connections, or raise ``DatabaseUnavailableError``.

    In Docker the app can start while the ``db`` container is still starting,
    restarting or stopped; its host name only resolves while it runs. Retrying
    with a clear log message beats crashing on the first attempt.
    """
    timeout = settings.db_connect_timeout_seconds if timeout_seconds is None else timeout_seconds
    target = f"{engine.url.host or 'localhost'}:{engine.url.port or 5432}/{engine.url.database or ''}"
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("Database %s is reachable (attempt %d).", target, attempt)
            return
        except OperationalError as exc:
            reason = str(exc.orig if getattr(exc, "orig", None) else exc).strip().splitlines()[0]
            if time.monotonic() + interval_seconds > deadline:
                raise DatabaseUnavailableError(
                    f"Could not connect to the database at {target} after {attempt} attempts "
                    f"({timeout:.0f}s): {reason}. "
                    "With Docker Compose, check that the db service is running and healthy "
                    "(`docker compose ps`, `docker compose logs db`) and start both together with "
                    "`docker compose up -d`. Outside Docker, check DATABASE_URL in .env."
                ) from exc
            logger.warning(
                "Database %s not reachable yet (%s); retrying in %.0fs (attempt %d).",
                target,
                reason,
                interval_seconds,
                attempt,
            )
            time.sleep(interval_seconds)

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


# Unique indexes that older databases (created before the constraints were added
# to the models) may be missing. Fresh databases get them from create_all().
UNIQUE_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "uq_pull_requests_repo_pr_number": ("pull_requests", ("repo_id", "pr_number")),
    "uq_commits_pr_commit_hash": ("commits", ("pr_id", "commit_hash")),
}


def _ensure_unique_indexes() -> None:
    for index_name, (table_name, columns) in UNIQUE_INDEXES.items():
        column_list = ", ".join(_quoted_identifier(column) for column in columns)
        statement = text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_quoted_identifier(index_name)} "
            f"ON {_quoted_identifier(table_name)} ({column_list})"
        )
        try:
            with engine.begin() as connection:
                connection.execute(statement)
        except (IntegrityError, ProgrammingError):
            logger.warning(
                "Could not create unique index %s on %s%s because the table already contains duplicate rows. "
                "Run scripts/clear_data.sql and re-sync to remove duplicates.",
                index_name,
                table_name,
                columns,
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
        "github_updated_at": _compile_column_definition(DateTime()),
        "merge_commit_sha": _compile_column_definition(String(64)),
        "base_branch": _compile_column_definition(String(255)),
        "state": _compile_column_definition(String(16)),
        "closed_at": _compile_column_definition(DateTime()),
        "head_branch": _compile_column_definition(String(255)),
        "github_id": _compile_column_definition(BigInteger()),
        "work_item_refs": _compile_column_definition(String(512)),
        "first_reviewed_at": _compile_column_definition(DateTime()),
        "approved_at": _compile_column_definition(DateTime()),
    }
    run_columns = {column["name"] for column in inspector.get_columns("azure_pipeline_runs")}
    run_alterations = {
        "build_work_item_ids": _compile_column_definition(String(2048)),
        "change_shas": _compile_column_definition(Text()),
        "change_work_item_refs": _compile_column_definition(String(2048)),
        "details_fetched_at": _compile_column_definition(DateTime()),
    }
    work_item_columns = {column["name"] for column in inspector.get_columns("azure_work_items")}
    work_item_alterations = {
        "parent_id": _compile_column_definition(Integer()),
        "history_changed_date": _compile_column_definition(DateTime()),
        "description": _compile_column_definition(Text()),
        "acceptance_criteria": _compile_column_definition(Text()),
    }
    commit_alterations = {
        "committed_at": _compile_column_definition(DateTime()),
    }
    pull_request_comment_alterations: dict[str, str] = {}

    with engine.begin() as connection:
        _apply_column_alterations(connection, "pull_requests", pull_request_columns, pull_request_alterations)
        _apply_column_alterations(connection, "commits", commit_columns, commit_alterations)
        _apply_column_alterations(connection, "azure_work_items", work_item_columns, work_item_alterations)
        _apply_column_alterations(connection, "azure_pipeline_runs", run_columns, run_alterations)
        _apply_column_alterations(
            connection,
            "pull_request_comments",
            pull_request_comment_columns,
            pull_request_comment_alterations,
        )

    _ensure_unique_indexes()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
