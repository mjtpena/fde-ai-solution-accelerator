import asyncio
import time
from typing import Literal

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal
from accelerator.identity.errors import AuthProviderUnavailable


class JsonWebKey(BaseModel):
    model_config = ConfigDict(extra="allow")

    kid: str
    kty: Literal["RSA"]


class JsonWebKeySet(BaseModel):
    keys: list[JsonWebKey]


class TokenClaims(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub: str = Field(min_length=1)
    oid: str | None = None
    roles: list[str] = Field(default_factory=list)


class EntraTokenValidator:
    def __init__(self, settings: Settings) -> None:
        self._tenant_id = settings.entra_tenant_id
        self._audience = settings.entra_audience
        self._issuer = f"https://login.microsoftonline.com/{self._tenant_id}/v2.0"
        self._jwks_uri = (
            f"https://login.microsoftonline.com/{self._tenant_id}/discovery/v2.0/keys"
        )
        self._keys: dict[str, JsonWebKey] = {}
        self._keys_expire_at = 0.0
        self._last_forced_refresh_at = 0.0
        self._refresh_lock = asyncio.Lock()

    async def validate(self, token: str, client: httpx.AsyncClient) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(key_id, str):
                raise ValueError("Unsupported token signing algorithm.")

            key = await self._get_key(key_id, client)
            if key is None:
                raise ValueError("Token signing key was not found.")

            signing_key = jwt.PyJWK.from_dict(key.model_dump()).key
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            parsed_claims = TokenClaims.model_validate(claims)
        except (jwt.PyJWTError, ValidationError, ValueError) as exc:
            raise ValueError("Invalid Entra access token.") from exc

        role_lookup = {role.value: role for role in AppRole}
        mapped_roles = frozenset(
            role_lookup[role_name]
            for role_name in parsed_claims.roles
            if role_name in role_lookup
        )
        return Principal(
            subject=parsed_claims.sub,
            object_id=parsed_claims.oid,
            roles=mapped_roles,
        )

    async def _get_key(
        self,
        key_id: str,
        client: httpx.AsyncClient,
    ) -> JsonWebKey | None:
        if time.monotonic() >= self._keys_expire_at:
            await self._refresh_keys(client)
        key = self._keys.get(key_id)
        if key is None:
            await self._refresh_keys(client, force=True)
        return self._keys.get(key_id)

    async def _refresh_keys(self, client: httpx.AsyncClient, *, force: bool = False) -> None:
        async with self._refresh_lock:
            now = time.monotonic()
            if now < self._keys_expire_at and (
                not force or now - self._last_forced_refresh_at < 60
            ):
                return
            try:
                response = await client.get(self._jwks_uri)
                response.raise_for_status()
                key_set = JsonWebKeySet.model_validate(response.json())
            except (httpx.HTTPError, ValidationError, ValueError) as exc:
                raise AuthProviderUnavailable from exc

            self._keys = {key.kid: key for key in key_set.keys}
            self._keys_expire_at = time.monotonic() + 300
            if force:
                self._last_forced_refresh_at = time.monotonic()
