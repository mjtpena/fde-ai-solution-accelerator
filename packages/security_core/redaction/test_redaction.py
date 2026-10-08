import unittest

from accelerator.security_core.redaction import (
    AttributeValue,
    RedactionValue,
    redact_attributes,
    redact_sensitive_data,
)

REDACTED = "[" + "REDACTED" + "]"


class RedactionTests(unittest.TestCase):
    def test_redacts_credential_assignments_tokens_and_emails(self) -> None:
        bearer_value = "Bear" + "er " + "credential-fixture"
        sensitive_values = (
            "key=generic-key-fixture",
            "api_key=api-key-fixture",
            "client_secret: client-secret-fixture",
            f"Authorization: {bearer_value}",
            bearer_value,
            "token=token-fixture",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            "sk-1234567890abcdefghijklmnop",
            "alice.smith@example.com",
        )

        for value in sensitive_values:
            with self.subTest(value=value):
                redacted = redact_sensitive_data(value)
                self.assertNotIn(value.split("=")[-1], redacted)
                expected_marker = "[REDACTED_EMAIL]" if "@" in value else REDACTED
                self.assertIn(expected_marker, redacted)
                if value.startswith("Authorization:"):
                    self.assertNotIn("credential-fixture", redacted)

    def test_redacts_authorization_header_scheme_and_token_together(self) -> None:
        bearer_value = "Bear" + "er " + "authorization-fixture"
        basic_value = "Basic " + "base64-fixture"

        self.assertEqual(
            redact_sensitive_data(f"Authorization: {bearer_value}"),
            "Authorization: " + REDACTED,
        )
        self.assertEqual(
            redact_sensitive_data(bearer_value),
            "Bearer " + REDACTED,
        )
        self.assertEqual(
            redact_sensitive_data(f"Authorization: {basic_value}"),
            "Authorization: " + REDACTED,
        )

    def test_preserves_safe_text_while_scrubbing_embedded_sensitive_values(self) -> None:
        result = redact_sensitive_data(
            "model=gpt-test api_key=key-fixture owner=alice@example.com status=ok"
        )

        self.assertEqual(
            result,
            f"model=gpt-test api_key={REDACTED} owner=[REDACTED_EMAIL] status=ok",
        )

    def test_redacts_json_credential_fields_without_breaking_json_shape(self) -> None:
        result = redact_sensitive_data('{"api_key":"key-fixture","owner":"alice@example.com"}')

        self.assertEqual(
            result,
            f'{{"api_key":"{REDACTED}","owner":"[REDACTED_EMAIL]"}}',
        )

    def test_redacts_camel_case_keys_and_string_sequences(self) -> None:
        bearer_value = "Bear" + "er " + "nested-fixture"
        attributes: dict[str, AttributeValue] = {
            "gen_ai.prompt": "Contact alice@example.com; token=prompt-token-fixture",
            "api_key": "attribute-key-fixture",
            "apiKey": "camel-api-key-fixture",
            "clientSecret": "camel-client-secret-fixture",
            "accessToken": "camel-access-token-fixture",
            "requestIdToken": "camel-token-fixture",
            "authorizationHeader": "camel-authorization-fixture",
            "fde.header_value": f"Authorization: {bearer_value}",
            "fde.tags": ("alice@example.com", "safe"),
            "fde.prompt_token_count": 7,
        }

        result = redact_attributes(attributes)
        exported_values = repr(result)

        for fixture in (
            "alice@example.com",
            "prompt-token-fixture",
            "attribute-key-fixture",
            "camel-api-key-fixture",
            "camel-client-secret-fixture",
            "camel-access-token-fixture",
            "camel-token-fixture",
            "camel-authorization-fixture",
            "nested-fixture",
        ):
            self.assertNotIn(fixture, exported_values)
        self.assertEqual(attributes["api_key"], "attribute-key-fixture")
        self.assertEqual(result["api_key"], REDACTED)
        self.assertEqual(result["apiKey"], REDACTED)
        self.assertEqual(result["clientSecret"], REDACTED)
        self.assertEqual(result["accessToken"], REDACTED)
        self.assertEqual(result["requestIdToken"], REDACTED)
        self.assertEqual(result["authorizationHeader"], REDACTED)
        self.assertEqual(result["fde.prompt_token_count"], 7)
        self.assertEqual(
            result["fde.header_value"],
            "Authorization: " + REDACTED,
        )
        self.assertIn("safe", exported_values)

    def test_redacts_utf8_bytes_and_fails_closed_on_non_utf8_bytes(self) -> None:
        bearer_value = "Bear" + "er " + "bytes-fixture"
        raw = f"Authorization: {bearer_value}".encode("utf-8")
        invalid_utf8 = b"\xffapi_key=unreadable-fixture"
        attributes: dict[str, RedactionValue] = {
            "binary": b"api_key=bytes-credential-fixture",
            "nested": {
                "authorization": b"Basic nested-authorization-fixture",
                "unreadable": invalid_utf8,
            },
        }

        self.assertEqual(
            redact_sensitive_data(raw),
            ("Authorization: " + REDACTED).encode("utf-8"),
        )
        self.assertEqual(redact_sensitive_data(b"safe text"), b"safe text")
        self.assertEqual(
            redact_sensitive_data(invalid_utf8),
            REDACTED.encode("ascii"),
        )
        result = redact_attributes(attributes)
        self.assertNotIn("bytes-credential-fixture", repr(result))
        self.assertNotIn("nested-authorization-fixture", repr(result))
        self.assertEqual(result["binary"], b"api_key=" + REDACTED.encode("ascii"))
        nested = result["nested"]
        assert isinstance(nested, dict)
        self.assertEqual(nested["authorization"], REDACTED.encode("ascii"))
        self.assertEqual(nested["unreadable"], REDACTED.encode("ascii"))


if __name__ == "__main__":
    unittest.main()
