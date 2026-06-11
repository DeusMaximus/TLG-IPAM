from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_key: str = "change-me"
    db_path: str = "./ipam.db"
    static_dir: str = "./static"
    version: str = "0.1.0"


settings = Settings()
