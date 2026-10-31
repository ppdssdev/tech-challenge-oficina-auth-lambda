import json
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def validator(name):
    schema = json.loads((ROOT / f"contracts/{name}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class ContractTests(unittest.TestCase):
    def test_request_shape(self):
        validator("request").validate({"cpf": "00000000000"})
        # Synthetic shape only: CPF check digits belong to the future handler.

    def test_rejects_invalid_requests(self):
        for value in [{}, {"cpf": 123}, {"cpf": "123"},
                      {"cpf": "000.000.000-00"},
                      {"cpf": "00000000000", "role": "ADMIN"}]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validator("request").validate(value)

    def test_success_contract(self):
        validator("response").validate({"access_token": "synthetic-token",
                                        "token_type": "Bearer", "expires_in": 900})

    def test_rejects_unbounded_expiry(self):
        with self.assertRaises(ValidationError):
            validator("response").validate({"access_token": "synthetic-token",
                                            "token_type": "Bearer", "expires_in": 0})

    def test_generic_error_contract(self):
        for code in ["INVALID_REQUEST", "AUTHENTICATION_FAILED", "RATE_LIMITED",
                     "TEMPORARILY_UNAVAILABLE"]:
            validator("error").validate({"code": code})
        with self.assertRaises(ValidationError):
            validator("error").validate({"code": "CUSTOMER_NOT_FOUND"})
