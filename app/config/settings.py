import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional, Any
from functools import lru_cache

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "AppHub API"
    APP_VERSION: str = "2.2.0"
    DEBUG: bool = False
    BASE_URL: str = ""  # e.g. https://apphubx.store/  (set in .env if needed)
    
    # Security
    SECRET_KEY: str = "change-this-to-a-secure-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # CORS
    CORS_ORIGINS: Any = "*"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: Any = "*"
    CORS_ALLOW_HEADERS: Any = "*"
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./scraper_v2.db"
    REDIS_ENABLED: bool = False
    
    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000
    
    # Monitoring & Logging
    ENABLE_METRICS: bool = True
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"

    # Compression
    ENABLE_GZIP: bool = True
    GZIP_MIN_SIZE: int = 1024
    
    # Scraping
    SCRAPER_TIMEOUT: int = 30
    SCRAPER_MAX_RETRIES: int = 3
    SCRAPER_RETRY_DELAY: int = 2
    
    # HLS Proxy
    HLS_PROXY_ENABLED: bool = True
    HLS_PROXY_TIMEOUT: int = 30

    # Static/CDN
    STATIC_CDN_BASE_URL: str = ""
    STATIC_CACHE_MAX_AGE: int = 3600
    STATIC_IMMUTABLE_MAX_AGE: int = 31536000
    STATIC_IMMUTABLE_PATTERNS: Any = [r"\.[a-f0-9]{8,}\."]
    
    # API Auth
    REQUIRE_AUTH: bool = False
    API_KEY_HEADER: str = "X-API-Key"

    # Robust Parser for CORS and other lists
    @field_validator("CORS_ORIGINS", "CORS_ALLOW_METHODS", "CORS_ALLOW_HEADERS", mode="after")
    @classmethod
    def parse_robust_list(cls, v: Any) -> list[str]:
        if isinstance(v, list): return v
        if not v or not isinstance(v, str): return ["*"]
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            try:
                import json
                return json.loads(v)
            except: pass
        return [i.strip() for i in v.split(",") if i.strip()]

    @field_validator("STATIC_IMMUTABLE_PATTERNS", mode="before")
    @classmethod
    def parse_static_immutable_patterns(cls, v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        if not v:
            return [r"\.[a-f0-9]{8,}\."]
        if isinstance(v, str):
            raw = v.strip()
            if raw.startswith("[") and raw.endswith("]"):
                try:
                    import json
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except:
                    pass
            return [item.strip() for item in raw.split(",") if item.strip()]
        return [str(v).strip()]

    # Pydantic Configuration
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        case_sensitive=False, 
        extra="ignore",
        env_ignore_empty=True
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
