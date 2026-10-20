from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "delta-match"
    DATABASE_URL: str = "sqlite:///./dev.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = 30

    # 短信验证码：开发模式直接返回固定码，不真发短信
    SMS_DEV_MODE: bool = True
    DEV_SMS_CODE: str = "6688"
    SMS_CODE_TTL_SECONDS: int = 300
    SMS_RESEND_INTERVAL_SECONDS: int = 60

    # 匹配费用（分）与取消退款比例
    MATCH_FEE_CENTS: int = 100
    REFUND_RATE: float = 0.8

    UPLOAD_DIR: str = "./uploads"


settings = Settings()
