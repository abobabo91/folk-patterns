"""Cloudflare R2 client + config. S3-compatible via boto3.

Credentials in tools/vault/vault.toml [apis.cloudflare_r2]. Env-var override
via R2_* if set.
"""
from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.client import Config


def _load_vault_creds() -> dict:
    """Read [apis.cloudflare_r2] from vault.toml."""
    vault_path = Path(__file__).resolve().parents[3] / "tools" / "vault" / "vault.toml"
    if not vault_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    data = tomllib.loads(vault_path.read_text(encoding="utf-8"))
    return (data.get("apis") or {}).get("cloudflare_r2") or {}


def get_config() -> dict:
    """Return {endpoint, access_key, secret_key, bucket, public_base}."""
    v = _load_vault_creds()
    return {
        "endpoint": os.environ.get("R2_ENDPOINT") or v.get("s3_endpoint"),
        "access_key": os.environ.get("R2_ACCESS_KEY_ID") or v.get("access_key_id"),
        "secret_key": os.environ.get("R2_SECRET_ACCESS_KEY") or v.get("secret_access_key"),
        "bucket": os.environ.get("R2_BUCKET") or v.get("bucket_name") or "folk-patterns",
        "public_base": os.environ.get("R2_PUBLIC_BASE") or v.get("public_base_url"),
    }


def client():
    """Return a boto3 S3 client bound to R2."""
    c = get_config()
    return boto3.client(
        "s3",
        endpoint_url=c["endpoint"],
        aws_access_key_id=c["access_key"],
        aws_secret_access_key=c["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def public_url(key: str) -> str:
    """Build the public URL for a given key. key = 'central-asia/uzbekistan/uzbek/textile/suzani/images/va_O360718.jpg'."""
    base = get_config()["public_base"]
    if not base:
        raise RuntimeError("public_base_url missing in vault. Add it under [apis.cloudflare_r2].")
    return f"{base.rstrip('/')}/{key.lstrip('/')}"
