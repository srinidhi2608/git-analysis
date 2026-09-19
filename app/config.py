from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    github_token: str = Field(..., env="GITHUB_TOKEN")
    developer_analytics_ai_url: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_URL")
    developer_analytics_ai_token: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_TOKEN")
    developer_analytics_ai_model: str | None = Field(default=None, env="DEVELOPER_ANALYTICS_AI_MODEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
