from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EF_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "your-model-name"
    tavily_api_key: str = ""
    max_tokens: int = Field(default=64000, ge=1000, le=200000)
    request_timeout: float = Field(default=60, ge=1, le=180)

    @property
    def live_available(self) -> bool:
        return bool(self.api_key and self.model and self.model != "your-model-name")

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
