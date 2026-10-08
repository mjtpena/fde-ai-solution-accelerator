"""Configuration for Foundry-backed evaluation."""

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class FoundryEvaluatorSettings(BaseSettings):
    """The Azure endpoint and deployed judge model used by Foundry evaluators."""

    model_config = SettingsConfigDict(
        env_prefix="EVALUATION_JUDGE_",
        extra="ignore",
        str_strip_whitespace=True,
    )

    azure_endpoint: HttpUrl
    azure_deployment: str = Field(min_length=1)
