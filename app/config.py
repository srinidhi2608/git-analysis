from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables or a ``.env`` file.

    Field names map to upper-case environment variables (e.g. ``database_url`` ->
    ``DATABASE_URL``). Unknown keys in ``.env`` (such as ``POSTGRES_PASSWORD``,
    which only docker compose uses) are ignored.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    github_token: str
    developer_analytics_ai_url: str | None = None
    developer_analytics_ai_token: str | None = None
    developer_analytics_ai_model: str | None = None

    # 3-tier LLM fallback settings
    online_ai_enabled: bool = False
    online_ai_api_key: str | None = None
    online_ai_model: str = "gemini-1.5-pro"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:14b"

    # Azure DevOps (Boards + Pipelines). Organization/projects live in config.yaml;
    # AZURE_DEVOPS_ORG is an optional fallback for the organization.
    azure_devops_pat: str | None = None
    azure_devops_org: str | None = None

    # Snapshots of the ingested data (see app/snapshot.py).
    snapshot_dir: str = "snapshots"
    # Load the newest snapshot at startup when the database has no pull requests.
    snapshot_autoload: bool = True
    # Save a snapshot automatically after every successful sync.
    snapshot_after_sync: bool = True

    # ---------------------------------------------------------------------------
    # Metric thresholds (all configurable via .env)
    # ---------------------------------------------------------------------------
    # A PR is considered "large" when its total changed lines exceed this value.
    large_pr_threshold_lines: int = 600
    # A first follow-up faster than this is treated as "good" responsiveness.
    followup_good_threshold_hours: float = 12.0
    # Percentage of PRs that required changes above which is flagged as a risk.
    requested_changes_risky_pct: float = 30.0
    # Average comments per PR above this threshold is treated as a "down" trend.
    high_comments_per_pr_threshold: float = 2.0
    # Average lead time (hours) below which is considered healthy in DORA view.
    lead_time_healthy_hours: float = 48.0
    # First review time (hours) below which is considered healthy.
    first_review_healthy_hours: float = 24.0
    # Review coverage rate (%) above which is considered good.
    review_coverage_good_pct: float = 80.0
    # Approval rate (%) above which is considered good.
    approval_rate_good_pct: float = 60.0
    # Change failure proxy rate (%) below which is considered acceptable.
    change_failure_acceptable_pct: float = 35.0
    # Minimum PRs/comments to raise analytics confidence from "low" to "medium".
    confidence_min_prs: int = 5
    confidence_min_comments: int = 8
    # Minimum PRs/comments to raise analytics confidence from "medium" to "high".
    confidence_high_prs: int = 8
    confidence_high_comments: int = 15


settings = Settings()
