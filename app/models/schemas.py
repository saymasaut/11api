# Pydantic Schemas for Request/Response Validation

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator
from typing import Any, Optional
from datetime import datetime


# ===== User Schemas =====

class UserBase(BaseModel):
    email: EmailStr
    username: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=100)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(UserBase):
    id: int
    role: str
    is_active: bool
    api_key: Optional[str] = None
    daily_quota: int
    requests_today: int
    total_requests: int
    created_at: datetime
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int


# ===== Scraping Schemas =====

class ScrapeRequest(BaseModel):
    url: HttpUrl

    @field_validator("url")
    @classmethod
    def validate_domain(cls, v: HttpUrl) -> HttpUrl:
        host = (v.host or "").lower()
        allowed_domains = [
            "xhamster.com",
            "masa49.org",
            "masa49.com",
            "masa49.cam",
            "xnxx.com",
            "xvideos.com",
            "pornhub.com",
            "youporn.com",
            "redtube.com",
            "beeg.com",
            "spankbang.com",
            "viralkand.com",
            "blowjobs.pro",
            "blackporn24.com",
            "lesbianporn8.net",
            "milfporn8.net",
            "indianporn365.xyz",
            "mmsbro.com",
            "thekamababa.com",
            "desimms2.site",
            "desiporn.one",
            "thotsporn.com",
            "leakedamateurporn.xyz",
            "zeenite.com",
            "uncutmaza.com",
            "uncutmazaa.com",
            "uncutmaza.cc",
            "mydesimms.watch",
            "85po.com",
            "cosxplay.com",
            "memojav.com",
            "hohoj.tv",
            "ggjav.com",
            "ggjav.tv",
            "porn87.com",
            "porn87.tv",
            "goodav17.com",
            "kanav.ad",
            "missav.ai",
            "jable.tv",
            "94mt.cc",
            "bindasmood.com",
            "eporner.com",
            "porntrex.com",
            "dotmaal.com",
            "maalcdn.com",
            "uncutmasti.com",
            "ixifile.xyz",
            "zmaal.net",
        ]
        if any(host.endswith(domain) for domain in allowed_domains):
            return v
        raise ValueError(f"Only {', '.join(allowed_domains)} URLs are allowed")


class ScrapeResponse(BaseModel):
    url: HttpUrl
    title: Optional[str] = None
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    uploader_name: Optional[str] = None
    uploader_avatar_url: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    upload_date: Optional[str] = None
    cached: bool = False  # Indicates if result came from cache


class VideoInfoResponse(ScrapeResponse):
    """GET /api/v1/videos/info — includes stream metadata omitted from ScrapeResponse."""

    preview_url: Optional[str] = None
    related_videos: list[dict[str, Any]] = Field(default_factory=list)
    video: dict[str, Any]
    playable: bool = True


class ListItem(BaseModel):
    url: HttpUrl
    title: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    uploader_name: Optional[str] = None
    uploader_avatar_url: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[list[str]] = None
    upload_date: Optional[str] = None


class ListRequest(BaseModel):
    base_url: HttpUrl

    @field_validator("base_url")
    @classmethod
    def validate_domain(cls, v: HttpUrl) -> HttpUrl:
        host = (v.host or "").lower()
        allowed_domains = [
            "xhamster.com",
            "masa49.org",
            "masa49.com",
            "masa49.cam",
            "xnxx.com",
            "xvideos.com",
            "pornhub.com",
            "youporn.com",
            "redtube.com",
            "beeg.com",
            "spankbang.com",
            "viralkand.com",
            "blowjobs.pro",
            "blackporn24.com",
            "lesbianporn8.net",
            "milfporn8.net",
            "indianporn365.xyz",
            "mmsbro.com",
            "thekamababa.com",
            "desimms2.site",
            "desiporn.one",
            "thotsporn.com",
            "leakedamateurporn.xyz",
            "zeenite.com",
            "uncutmaza.com",
            "uncutmazaa.com",
            "uncutmaza.cc",
            "mydesimms.watch",
            "85po.com",
            "cosxplay.com",
            "memojav.com",
            "hohoj.tv",
            "ggjav.com",
            "ggjav.tv",
            "porn87.com",
            "porn87.tv",
            "goodav17.com",
            "kanav.ad",
            "missav.ai",
            "jable.tv",
            "94mt.cc",
            "bindasmood.com",
            "eporner.com",
            "porntrex.com",
            "dotmaal.com",
            "maalcdn.com",
            "uncutmasti.com",
            "ixifile.xyz",
            "zmaal.net",
        ]
        if any(host.endswith(domain) for domain in allowed_domains):
            return v
        raise ValueError(f"Only {', '.join(allowed_domains)} base_url are allowed")


# ===== Category Schemas =====

class CategoryItem(BaseModel):
    name: str
    url: str
    video_count: Optional[int] = 0


# ===== Job Schemas =====

class JobCreate(BaseModel):
    job_type: str = Field(..., pattern="^(scrape|crawl|batch)$")
    parameters: dict


class JobResponse(BaseModel):
    id: int
    job_id: str
    job_type: str
    status: str
    progress: int
    parameters: dict
    result: Optional[dict] = None
    error: Optional[str] = None
    items_processed: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: int
    items_processed: int
    error: Optional[str] = None


# ===== Stats Schemas =====

class UsageStats(BaseModel):
    total_requests: int
    successful_requests: int
    failed_requests: int
    scrape_requests: int
    list_requests: int
    crawl_requests: int
    unique_users: int
    cache_hit_rate: Optional[float] = None
    avg_response_time: Optional[float] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    timestamp: datetime
    uptime: Optional[float] = None


class DetailedHealthResponse(HealthResponse):
    database: bool
    redis: bool
    celery: bool
    dependencies: dict


# ===== Admin Schemas =====

class UpdateQuota(BaseModel):
    daily_quota: int = Field(..., ge=0, le=100000)


class ClearCacheRequest(BaseModel):
    pattern: Optional[str] = None  # Clear specific pattern or all


# ===== Notification Schemas =====

class NotificationItem(BaseModel):
    id: str
    title: str
    message: str
    type: str = "info" # info, warning, success, error
    icon: Optional[str] = None
    action_text: Optional[str] = None
    action_url: Optional[str] = None
    is_dismissible: bool = True
    created_at: datetime = Field(default_factory=datetime.now)

class NotificationResponse(BaseModel):
    notifications: list[NotificationItem]
    total: int
