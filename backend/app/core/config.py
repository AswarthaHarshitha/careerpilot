from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

INSECURE_DEV_SECRET = "dev-only-insecure-secret-change-me-0000000000"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./var/careerpilot.db"

    # Signs session tokens. Must be long and random in production.
    jwt_secret: str = Field(default=INSECURE_DEV_SECRET, validation_alias=AliasChoices("SECRET_KEY", "JWT_SECRET"))
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt API keys and tokens the user stores.
    secret_encryption_key: str = ""

    # Registration is open until the first account exists unless explicitly enabled.
    allow_registration: bool = False
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    public_web_url: str = "http://localhost:5173"

    # Outbound fetching of job pages and GitHub.
    fetch_timeout_seconds: float = 20.0
    fetch_max_bytes: int = 5_000_000
    # Hosts that bypass the private-network block. Only for local test fixtures; never set in production.
    fetch_allow_private_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Render JavaScript-built career pages in a sandboxed headless browser when their HTML is empty.
    browser_render_enabled: bool = True
    github_api_base: str = "https://api.github.com"
    github_raw_base: str = "https://raw.githubusercontent.com"
    github_token: str = ""  # optional server-wide token; users can also store their own

    # AI providers. Every AI-assisted step also has a deterministic path, so none of these is required.
    # Keys live only on the server; the web app never sees them.
    gemini_api_key: str = ""
    gemini_model: str = ""  # empty: pick the current Flash model from the provider's model list
    openrouter_api_key: str = ""
    openrouter_model: str = ""  # empty: use OpenRouter's free-model router or a currently listed free model
    ollama_base_url: str = ""  # e.g. http://localhost:11434 when Ollama runs next to the API
    ollama_model: str = ""  # empty: the first model Ollama has installed
    # Order in which configured providers are tried when a user has not chosen one.
    ai_provider_order: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["gemini", "openrouter", "ollama"]
    )
    llm_timeout_seconds: float = 60.0

    # Resume rendering.
    latex_engine: Literal["auto", "tectonic", "pdflatex"] = "auto"
    latex_timeout_seconds: int = 120
    soffice_path: str = ""

    upload_max_bytes: int = 2_000_000
    worker_enabled: bool = True
    worker_poll_seconds: float = 1.0
    worker_threads: int = 2

    @field_validator("cors_origins", "fetch_allow_private_hosts", "ai_provider_order", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Hosted providers hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @model_validator(mode="after")
    def _production_guards(self):
        if self.app_env == "production":
            if self.jwt_secret == INSECURE_DEV_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("SECRET_KEY must be set to a random value of at least 32 characters in production")
            if not self.secret_encryption_key:
                raise ValueError("SECRET_ENCRYPTION_KEY must be set in production")
            if self.fetch_allow_private_hosts:
                raise ValueError("FETCH_ALLOW_PRIVATE_HOSTS must be empty in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
