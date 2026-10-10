from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    github_token: str = Field(..., env="GITHUB_TOKEN")
    developer_analytics_ai_url: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_URL")
    developer_analytics_ai_token: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_TOKEN")
    developer_analytics_ai_model: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_MODEL")

    # 3-tier LLM fallback settings
    online_ai_enabled: bool = Field(default=False, env="ONLINE_AI_ENABLED")
    online_ai_api_key: str | None = Field(default=None, env="ONLINE_AI_API_KEY")
    online_ai_model: str = Field(default="gemini-1.5-pro", env="ONLINE_AI_MODEL")
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5-coder:14b", env="OLLAMA_MODEL")

    # ---------------------------------------------------------------------------
    # Metric thresholds (all configurable via .env)
    # ---------------------------------------------------------------------------
    # A PR is considered "large" when its total changed lines exceed this value.
    large_pr_threshold_lines: int = Field(default=600, env="LARGE_PR_THRESHOLD_LINES")
    # A first follow-up faster than this is treated as "good" responsiveness.
    followup_good_threshold_hours: float = Field(default=12.0, env="FOLLOWUP_GOOD_THRESHOLD_HOURS")
    # Percentage of PRs that required changes above which is flagged as a risk.
    requested_changes_risky_pct: float = Field(default=30.0, env="REQUESTED_CHANGES_RISKY_PCT")
    # Average comments per PR above this threshold is treated as a "down" trend.
    high_comments_per_pr_threshold: float = Field(default=2.0, env="HIGH_COMMENTS_PER_PR_THRESHOLD")
    # Average lead time (hours) below which is considered healthy in DORA view.
    lead_time_healthy_hours: float = Field(default=48.0, env="LEAD_TIME_HEALTHY_HOURS")
    # First review time (hours) below which is considered healthy.
    first_review_healthy_hours: float = Field(default=24.0, env="FIRST_REVIEW_HEALTHY_HOURS")
    # Review coverage rate (%) above which is considered good.
    review_coverage_good_pct: float = Field(default=80.0, env="REVIEW_COVERAGE_GOOD_PCT")
    # Approval rate (%) above which is considered good.
    approval_rate_good_pct: float = Field(default=60.0, env="APPROVAL_RATE_GOOD_PCT")
    # Change failure proxy rate (%) below which is considered acceptable.
    change_failure_acceptable_pct: float = Field(default=35.0, env="CHANGE_FAILURE_ACCEPTABLE_PCT")
    # Minimum PRs/comments to raise analytics confidence from "low" to "medium".
    confidence_min_prs: int = Field(default=5, env="CONFIDENCE_MIN_PRS")
    confidence_min_comments: int = Field(default=8, env="CONFIDENCE_MIN_COMMENTS")
    # Minimum PRs/comments to raise analytics confidence from "medium" to "high".
    confidence_high_prs: int = Field(default=8, env="CONFIDENCE_HIGH_PRS")
    confidence_high_comments: int = Field(default=15, env="CONFIDENCE_HIGH_COMMENTS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
