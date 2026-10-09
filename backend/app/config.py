from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: Optional[str] = None
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    cors_origins: str = "http://localhost:3000"

    # Doctor collector (jobs/daily_check.py)
    osm_enabled: bool = True                    # OpenStreetMap: free, no key
    osm_governorates: str = "all"               # "all", or codes such as "cairo,giza"
    osm_every_days: int = 1
    # Google Places: optional and paid; off while the key is empty
    google_places_api_key: Optional[str] = None
    google_places_max_requests: int = 200       # per run; every request is billed
    google_places_governorates: str = "cairo,giza,alexandria"
    google_places_max_pages: int = 2            # up to 20 results per page
    google_discovery_every_days: int = 7
    google_refresh_every_days: int = 7          # Google content must not be kept unrefreshed
    stale_after_misses: int = 3

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_places_governorate_list(self) -> List[str]:
        return [g.strip() for g in self.google_places_governorates.split(",") if g.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
