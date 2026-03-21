from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://readonly_user:password@localhost:5432/safety_db"
    app_name: str = "Safety Management Sample"
    debug: bool = False

    model_config = {"env_file": ".env"}


settings = Settings()
