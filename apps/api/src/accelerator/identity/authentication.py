from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from accelerator.identity.errors import AuthProviderUnavailable

if TYPE_CHECKING:
    from accelerator.identity.jwt_validator import EntraTokenValidator


class AppRole(str, Enum):
    READER = "Reader"
    CONTRIBUTOR = "Contributor"
    APPROVER = "Approver"
    ADMIN = "Admin"


class Principal(BaseModel):
    subject: str
    object_id: str | None = None
    roles: frozenset[AppRole] = Field(default_factory=frozenset)


bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    validator: EntraTokenValidator = request.app.state.token_validator
    try:
        return await validator.validate(
            credentials.credentials,
            request.app.state.http_client,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except AuthProviderUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider keys are temporarily unavailable.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def require_any_role(
    *required_roles: AppRole,
) -> Callable[..., Awaitable[Principal]]:
    if not required_roles:
        raise ValueError("At least one app role must be required.")

    async def authorize(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        if principal.roles.isdisjoint(required_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient app role.",
            )
        return principal

    return authorize
