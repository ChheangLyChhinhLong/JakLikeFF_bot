from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    telegram_admin_ids: str = ""
    admin_group_id: int | None = None
    supabase_url: str
    supabase_service_role_key: str
    khpay_api_key: str
    khpay_base_url: str = "https://khpay.site/api/v1"
    khpay_webhook_secret: str = ""
    khpay_webhook_url: str = ""
    reseller_api_url: str = "https://mean-like.com/autolikeff"
    reseller_seller_id: str = "8916235907"
    reseller_api_key: str = ""
    reseller_daily_batch_likes: int = Field(default=220, gt=0)
    support_username: str = ""
    poll_interval_seconds: int = 20
    port: int = 10000

    @property
    def admin_ids(self) -> set[int]:
        return {int(value.strip()) for value in self.telegram_admin_ids.split(",") if value.strip()}

    @property
    def admin_chat_id(self) -> int | None:
        return self.admin_group_id

@lru_cache
def get_settings() -> Settings:
    return Settings()
