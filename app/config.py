from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    github_token: str = Field(..., env="GITHUB_TOKEN")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
