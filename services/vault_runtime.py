"""Resolve configured environment bindings once, before application imports."""

import os
import re

from services.infisical_store import InfisicalStore, VaultUnavailable, private_config


def load_runtime_secrets(config_path=None):
    path = config_path or os.getenv("CURIE_VAULT_CONFIG")
    if not path:
        return
    config = private_config(path)
    if not isinstance(config.get("bindings"), list) or not config["bindings"]:
        raise VaultUnavailable()
    values = {}
    for binding in config.get("bindings", []):
        store_config = private_config(binding["accessConfig"])
        store_config["secretPath"] = binding["secretPath"]
        client = InfisicalStore(store_config)
        for destination, key in binding["environment"].items():
            if (
                not re.fullmatch(r"[A-Z][A-Z0-9_]*", destination)
                or destination
                in {
                    "PATH",
                    "HOME",
                    "PYTHONPATH",
                    "PYTHONSTARTUP",
                    "LD_PRELOAD",
                    "BASH_ENV",
                    "NODE_OPTIONS",
                }
                or destination.startswith(("LD_", "DYLD_"))
            ):
                raise VaultUnavailable()
            value = client.get(key)
            if value is None or not value:
                raise VaultUnavailable()
            values[destination] = value
    # A failed fetch must not leave a partially updated runtime configuration.
    os.environ.update(values)
    if config.get("credentialConfig"):
        os.environ["CURIE_CREDENTIAL_BACKEND"] = "infisical"
        os.environ["CURIE_VAULT_CREDENTIAL_CONFIG"] = config["credentialConfig"]
