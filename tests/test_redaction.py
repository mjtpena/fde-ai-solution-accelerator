import unittest

from accelerator.security_core.redaction import (
    AttributeValue,
    redact_attributes,
    redact_sensitive_data,
)


class RedactionTests(unittest.TestCase):
    def test_redacts_credential_assignments_tokens_and_emails(self) -> None:
        sensitive_values = (
            "key=generic-key-fixture",
            "api_key=api-key-fixture",
            "client_secret: client-secret-fixture",
            "Authorization: Bearer bearer-token-fixture",
            "token=token-fixture",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            "sk-1234567890abcdefghijklmnop",
            "alice.smith@example.com",
        )

        for value in sensitive_values:
            with self.subTest(value=value):
                redacted = redact_sensitive_data(value)
                self.assertNotIn(value.split("=")[-1], redacted)
                self.assertIn("[REDACTED", redacted)

    def test_preserves_safe_text_while_scrubbing_embedded_sensitive_values(self) -> None:
        result = redact_sensitive_data(
            "model=gpt-test api_key=key-fixture owner=alice@example.com status=ok"
        )

        self.assertEqual(
            result,
            "model=gpt-test api_key=[REDACTED] owner=[REDACTED_EMAIL] status=ok",
        )

    def test_redacts_json_credential_fields_without_breaking_json_shape(self) -> None:
        result = redact_sensitive_data('{"api_key":"key-fixture","owner":"alice@example.com"}')

        self.assertEqual(
            result,
            '{"api_key":"[REDACTED]","owner":"[REDACTED_EMAIL]"}',
        )

    def test_redacts_nested_span_attributes_without_mutating_input(self) -> None:
        attributes: dict[str, AttributeValue] = {
            "gen_ai.prompt": "Contact alice@example.com; token=prompt-token-fixture",
            "api_key": "attribute-key-fixture",
            "fde.details": {
                "email": "bob@example.org",
                "http.request.header.authorization": "Bearer nested-token-fixture",
                "safe": "trace-created",
            },
            "fde.tags": ("alice@example.com", "safe"),
            "fde.prompt_token_count": 7,
        }

        result = redact_attributes(attributes)
        exported_values = repr(result)

        for fixture in (
            "alice@example.com",
            "prompt-token-fixture",
            "attribute-key-fixture",
            "bob@example.org",
            "nested-token-fixture",
        ):
            self.assertNotIn(fixture, exported_values)
        self.assertEqual(attributes["api_key"], "attribute-key-fixture")
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertEqual(result["fde.prompt_token_count"], 7)
        self.assertIn("trace-created", exported_values)
        self.assertIn("safe", exported_values)


if __name__ == "__main__":
    unittest.main()
