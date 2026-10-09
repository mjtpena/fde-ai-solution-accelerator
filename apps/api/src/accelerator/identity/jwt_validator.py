import asyncio
import logging
import time
from collections.abc import Callable
import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from accelerator.configuration.settings import Settings
from accelerator.identity.authentication import AppRole, Principal
from accelerator.identity.errors import AuthProviderUnavailable

logger = logging.getLogger(__name__)

SIGNING_ALGORITHM = "RS256"
# After a failed refresh, wait this long before contacting the provider again.
REFRESH_RETRY_SECONDS = 30.0
# Unknown-kid forced refreshes are throttled so forged kids cannot hammer Entra.
FORCED_REFRESH_INTERVAL_SECONDS = 60.0


class TokenClaims(BaseModel):
    model_config = ConfigDict(extra="allow")

    sub: str = Field(min_length=1)
    oid: str | None = None
    roles: list[str] = Field(default_factory=list)


class JwksDocument(BaseModel):
    """The JWKS envelope; each entry is validated on its own so one bad key is skipped."""

    keys: list[object]


class JwkCandidate(BaseModel):
    model_config = ConfigDict(extra="allow")

    kty: str
    kid: str = Field(min_length=1)
    use: str = "sig"
    alg: str = SIGNING_ALGORITHM
    key_ops: list[str] | None = None

    @property
    def verifies_rs256(self) -> bool:
        return (
            self.kty == "RSA"
            and self.use == "sig"
            and self.alg == SIGNING_ALGORITHM
            and (self.key_ops is None or "verify" in self.key_ops)
        )


def _signing_keys(document: JwksDocument, correlation_id: str | None) -> dict[str, jwt.PyJWK]:
    """Usable RS256 verification keys by kid; other and malformed keys are skipped."""
    keys: dict[str, jwt.PyJWK] = {}
    for raw in document.keys:
        try:
            candidate = JwkCandidate.model_validate(raw)
        except ValidationError:
            continue  # not a key this validator could use
        if not candidate.verifies_rs256:
            continue
        try:
            keys[candidate.kid] = jwt.PyJWK.from_dict(
                candidate.model_dump(exclude_none=True), algorithm=SIGNING_ALGORITHM
            )
        except jwt.PyJWTError:
            logger.warning(
                "jwks_key_skipped",
                extra={"kid": candidate.kid, "correlation_id": correlation_id},
            )
    return keys


class EntraTokenValidator:
    """Validate Entra access tokens against the tenant's published signing keys.

    Keys are cached for ``jwks_cache_seconds``. When a refresh fails, previously
    fetched keys keep being served for up to ``jwks_max_stale_seconds`` so a brief
    identity-provider outage does not reject every request; with no usable keys the
    validator raises ``AuthProviderUnavailable`` (503), never a 401.
    """

    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._audience = settings.entra_audience
        self._issuer = settings.entra_issuer_url
        self._jwks_uri = settings.entra_jwks_url
        self._leeway = settings.jwt_leeway_seconds
        self._cache_seconds = settings.jwks_cache_seconds
        self._max_stale_seconds = settings.jwks_max_stale_seconds
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._next_refresh_at = 0.0
        self._last_forced_refresh_at = -FORCED_REFRESH_INTERVAL_SECONDS
        self._last_refresh_failed = False
        self._refresh_lock = asyncio.Lock()

    async def validate(
        self, token: str, client: httpx.AsyncClient, *, correlation_id: str | None = None
    ) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if header.get("alg") != SIGNING_ALGORITHM:
                raise ValueError("Unsupported token signing algorithm.")
            if not isinstance(key_id, str) or not key_id:
                raise ValueError("Token has no key ID.")

            key = await self._get_key(key_id, client, correlation_id)
            if key is None:
                raise ValueError("Token signing key was not found.")

            claims = jwt.decode(
                token,
                key.key,
                algorithms=[SIGNING_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
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

    async def ensure_signing_keys(
        self, client: httpx.AsyncClient, *, correlation_id: str | None = None
    ) -> None:
        """Raise ``AuthProviderUnavailable`` unless usable signing keys are cached or fetchable."""
        if self._clock() >= self._next_refresh_at:
            await self._refresh_keys(client, correlation_id)
        self._usable_keys()

    async def _get_key(
        self, key_id: str, client: httpx.AsyncClient, correlation_id: str | None
    ) -> jwt.PyJWK | None:
        if self._clock() >= self._next_refresh_at:
            await self._refresh_keys(client, correlation_id)
        keys = self._usable_keys()
        if key_id not in keys:
            await self._refresh_keys(client, correlation_id, force=True)
            keys = self._usable_keys()
            if key_id not in keys and self._last_refresh_failed:
                # The provider could not confirm the key is unknown, e.g. a rotation
                # during an outage: that is unavailability, not an invalid token.
                raise AuthProviderUnavailable
        return keys.get(key_id)

    def _usable_keys(self) -> dict[str, jwt.PyJWK]:
        if self._fetched_at is None or not self._keys:
            raise AuthProviderUnavailable
        if self._clock() - self._fetched_at > self._max_stale_seconds:
            raise AuthProviderUnavailable
        return self._keys

    async def _refresh_keys(
        self, client: httpx.AsyncClient, correlation_id: str | None, *, force: bool = False
    ) -> None:
        async with self._refresh_lock:
            now = self._clock()
            if force:
                if now - self._last_forced_refresh_at < FORCED_REFRESH_INTERVAL_SECONDS:
                    return
                self._last_forced_refresh_at = now
            elif now < self._next_refresh_at:
                return  # another request refreshed while this one waited
            try:
                response = await client.get(self._jwks_uri)
                response.raise_for_status()
                keys = _signing_keys(
                    JwksDocument.model_validate(response.json()), correlation_id
                )
                if not keys:
                    raise ValueError("JWKS document has no usable RS256 signing keys.")
            except (httpx.HTTPError, ValueError) as exc:
                self._last_refresh_failed = True
                self._next_refresh_at = now + REFRESH_RETRY_SECONDS
                logger.warning(
                    "jwks_refresh_failed",
                    extra={
                        "exception_type": type(exc).__name__,
                        "serving_stale_keys": bool(self._keys),
                        "correlation_id": correlation_id,
                    },
                )
                return
            self._last_refresh_failed = False
            self._keys = keys
            self._fetched_at = now
            self._next_refresh_at = now + self._cache_seconds
