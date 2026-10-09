"""Managed-identity database auth sends its token only over verified TLS."""

import os
import ssl
from typing import Any

import pytest
from azure.core.credentials import AccessToken
from sqlalchemy import text
from sqlalchemy.engine import make_url

from accelerator.ingestion.infrastructure.database import create_engine


class PasswordAsToken:
    """Stands in for an Entra credential: the test server's password is the token."""

    def __init__(self, password: str) -> None:
        self.password = password

    async def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        return AccessToken(self.password, 4_102_444_800)


async def test_managed_identity_connects_only_to_a_trusted_server(postgres_dsn: str) -> None:
    ca_file = os.environ.get("TEST_POSTGRES_CA_FILE")
    if not ca_file:
        pytest.skip("Set TEST_POSTGRES_CA_FILE to the test server's certificate.")
    url = make_url(postgres_dsn)
    password = url.password or ""
    without_password = url.set(password=None).render_as_string(hide_password=False)

    trusted = create_engine(
        without_password,
        managed_identity=True,
        credential=PasswordAsToken(password),  # type: ignore[arg-type]
        tls_ca_file=ca_file,
    )
    try:
        async with trusted.connect() as connection:
            encrypted = await connection.execute(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            )
            assert encrypted.scalar_one() is True
    finally:
        await trusted.dispose()

    # The test server's self-signed certificate is not in the system trust store.
    untrusted = create_engine(
        without_password,
        managed_identity=True,
        credential=PasswordAsToken(password),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            async with untrusted.connect():
                pass
    finally:
        await untrusted.dispose()
