from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    season_id: int = 44
    team_a_id: int = 1977
    team_b_id: int = 17541
    # iBIS har flyttat sina endpoints en gång (från /v2/api till /v2/api/public).
    # Ligger därför i konfiguration så att nästa flytt bara kräver en miljövariabel.
    ibis_base_url: str = "https://api.innebandy.se/v2/api/public"
    app_password: str
    database_url: str = "sqlite:///./tungelsta.db"


settings = Settings()
