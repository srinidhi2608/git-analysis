from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PullRequest


def get_pull_request_counts_by_user_story(db: Session):
    """Return the count of pull requests grouped by user story ID."""
    stmt = (
        select(
            PullRequest.user_story_id,
            func.count(PullRequest.id).label("pull_request_count"),
        )
        .where(PullRequest.user_story_id.is_not(None))
        .group_by(PullRequest.user_story_id)
        .order_by(PullRequest.user_story_id)
    )
    return db.execute(stmt).all()
