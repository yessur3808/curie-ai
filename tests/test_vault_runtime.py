import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit

from services import credential_vault
from services.infisical_store import InfisicalStore, VaultUnavailable, private_config
from services.vault_runtime import load_runtime_secrets


class Response(io.BytesIO):
    pass


class Transport:
    def __init__(self):
        self.values = {}
        self.logins = 0
        self.denied = False
        self.hidden = False
        self.requests = []
        self.next_status = None

    def open(self, request, timeout):
        self.requests.append(request)
        path = urlsplit(request.full_url).path
        if self.next_status:
            status, self.next_status = self.next_status, None
            raise HTTPError(
                request.full_url,
                status,
                "secret-must-not-leak",
                {},
                io.BytesIO(b"secret-must-not-leak"),
            )
        if path.endswith("/login"):
            self.logins += 1
            return Response(
                json.dumps({"accessToken": "unit-access", "expiresIn": 900}).encode()
            )
        if self.denied:
            raise HTTPError(request.full_url, 403, "secret-must-not-leak", {}, None)
        key = unquote(path.split("/")[-1])
        if request.method in ("GET", "PATCH", "DELETE") and key not in self.values:
            raise HTTPError(request.full_url, 404, "missing", {}, None)
        if request.method in ("POST", "PATCH"):
            self.values[key] = json.loads(request.data)["secretValue"]
        if request.method == "DELETE":
            del self.values[key]
            return Response(b"{}")
        return Response(
            json.dumps(
                {
                    "secret": {
                        "secretValue": self.values[key],
                        "secretValueHidden": self.hidden,
                    }
                }
            ).encode()
        )


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport()
        self.store = InfisicalStore(
            {
                "projectId": "unit-project",
                "serviceToken": "unit-only-token",
                "secretPath": "/curie/credentials",
            },
            opener=self.transport,
        )

    def test_crud_and_missing_record(self):
        self.assertIsNone(self.store.get("missing"))
        self.store.set("key", '{"refresh_token":"first"}')
        self.store.set("key", '{"refresh_token":"rotated"}')
        self.assertEqual(json.loads(self.store.get("key"))["refresh_token"], "rotated")
        self.assertTrue(self.store.delete("key"))
        self.assertFalse(self.store.delete("key"))

    def test_authentication_is_reused_and_retried_only_once(self):
        self.store.config.pop("serviceToken")
        self.store.config.update(
            clientId="unit-id", clientSecret="unit-secret"  # pragma: allowlist secret
        )
        self.store._token = None
        self.store._expires = 0
        self.store.get("missing")
        self.store.get("missing")
        self.assertEqual(self.transport.logins, 1)
        self.transport.next_status = 401
        self.store.get("missing")
        self.assertEqual(self.transport.logins, 2)

    def test_denied_reads_do_not_become_missing_and_errors_are_redacted(self):
        self.transport.denied = True
        with self.assertRaises(VaultUnavailable) as result:
            self.store.get("key")
        self.assertEqual(result.exception.status, 403)
        self.assertNotIn("secret-must-not-leak", str(result.exception))
        self.assertNotIn("unit-only-token", str(result.exception))

    def test_hidden_values_and_redirects_fail_closed(self):
        self.transport.values["key"] = "value"
        self.transport.hidden = True
        with self.assertRaises(VaultUnavailable):
            self.store.get("key")
        self.transport.next_status = 302
        with self.assertRaises(VaultUnavailable):
            self.store.get("key")

    def test_bootstrap_configuration_must_be_private_and_not_symlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_text("{}")
            path.chmod(0o600)
            self.assertEqual(private_config(path), {})
            path.chmod(0o644)
            with self.assertRaises(VaultUnavailable):
                private_config(path)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                private_config(link)

    def test_oauth_records_refresh_without_local_file_and_remain_owner_scoped(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {
                    "CURIE_CREDENTIAL_BACKEND": "infisical",
                    "CURIE_VAULT_CREDENTIAL_CONFIG": "unit-config",
                    "CURIE_CREDENTIAL_STORE": directory + "/must-not-exist",
                },
            ),
            patch.object(InfisicalStore, "from_file", return_value=self.store),
        ):
            credential_vault.put_credential("owner-a", "x", {"refresh_token": "first"})
            credential_vault.put_credential(
                "owner-a", "x", {"refresh_token": "rotated"}
            )
            self.assertEqual(
                credential_vault.get_credential("owner-a", "x"),
                {"refresh_token": "rotated"},
            )
            self.assertIsNone(credential_vault.get_credential("owner-b", "x"))
            self.assertIsNone(
                credential_vault.get_credential("owner-a", "google:gmail")
            )
            self.assertFalse(Path(directory, "must-not-exist").exists())
            self.transport.denied = True
            with patch.object(
                credential_vault, "_load", side_effect=AssertionError("local fallback")
            ):
                with self.assertRaises(VaultUnavailable):
                    credential_vault.get_credential("owner-a", "x")

    def test_environment_bindings_are_all_or_nothing(self):
        config = {
            "bindings": [
                {
                    "accessConfig": "access",
                    "secretPath": "/curie",
                    "environment": {
                        "TELEGRAM_BOT_TOKEN": "telegram",
                        "GOVEE_API_KEY": "govee",  # pragma: allowlist secret
                    },
                }
            ]
        }
        access = {"projectId": "unit-project", "serviceToken": "unit-token"}
        self.transport.values["telegram"] = "new-token"
        with (
            patch(
                "services.vault_runtime.private_config", side_effect=[config, access]
            ),
            patch("services.vault_runtime.InfisicalStore", return_value=self.store),
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "original"}),
        ):
            with self.assertRaises(VaultUnavailable):
                load_runtime_secrets("config")
            self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "original")

    def test_unknown_backend_never_silently_uses_files(self):
        with patch.dict(os.environ, {"CURIE_CREDENTIAL_BACKEND": "misspelled"}):
            with self.assertRaises(RuntimeError):
                credential_vault.get_credential("owner", "x")

    def test_complete_environment_binding_selects_remote_credentials(self):
        config = {
            "bindings": [
                {
                    "accessConfig": "access",
                    "secretPath": "/curie",
                    "environment": {"TELEGRAM_BOT_TOKEN": "telegram"},
                }
            ],
            "credentialConfig": "/private/curie-tokens.json",
        }
        self.transport.values["telegram"] = "vault-token"
        with (
            patch("services.vault_runtime.private_config", side_effect=[config, {}]),
            patch("services.vault_runtime.InfisicalStore", return_value=self.store),
            patch.dict(os.environ, {}, clear=True),
        ):
            load_runtime_secrets("config")
            self.assertEqual(os.environ["TELEGRAM_BOT_TOKEN"], "vault-token")
            self.assertEqual(os.environ["CURIE_CREDENTIAL_BACKEND"], "infisical")
            self.assertEqual(
                os.environ["CURIE_VAULT_CREDENTIAL_CONFIG"],
                "/private/curie-tokens.json",
            )

    def test_environment_binding_cannot_inject_process_options(self):
        config = {
            "bindings": [
                {
                    "accessConfig": "access",
                    "secretPath": "/curie",
                    "environment": {"LD_PRELOAD": "key"},
                }
            ]
        }
        with (
            patch("services.vault_runtime.private_config", side_effect=[config, {}]),
            patch("services.vault_runtime.InfisicalStore", return_value=self.store),
        ):
            with self.assertRaises(VaultUnavailable):
                load_runtime_secrets("config")
        self.assertEqual(self.transport.requests, [])

    def test_pending_approval_is_not_reported_as_saved(self):
        with patch.object(
            self.store, "request", return_value={"approval": {"status": "pending"}}
        ):
            with self.assertRaises(VaultUnavailable):
                self.store.set("key", "value")
            with self.assertRaises(VaultUnavailable):
                self.store.delete("key")


if __name__ == "__main__":
    unittest.main()
