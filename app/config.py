from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    season_id: int = 44
    team_a_id: int = 1977
    team_b_id: int = 17541
    app_password: str
    database_url: str = "sqlite:///./tungelsta.db"


settings = Settings()
