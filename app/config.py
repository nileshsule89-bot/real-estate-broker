from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Real Estate Broker Agent"
    environment: str = "development"
    database_url: str = "sqlite:///./real_estate.db"
    llm_provider: str = "openrouter"
    openrouter_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    nvidia_api_url: str = "https://integrate.api.nvidia.com/v1/chat/completions"
    nvidia_api_key: str = ""
    nvidia_model: str = "gpt-35-turbo"
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "dev-verify-token"
    superbase_api_key: str = ""
    superbase_url: str = ""
    superbase_storage_url: str = ""
    superbase_bucket: str = ""
    superbase_project_id: str = ""
    field_agent_name: str = "Nilesh Sule"
    field_agent_phone: str = "9702044168"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
