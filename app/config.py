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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
