from __future__ import annotations

import os
from typing import Any

from .database import Repository
from .secrets import SecretCipher


class ProviderConfigurationError(ValueError):
    pass


def key_hint(api_key: str) -> str:
    stripped = api_key.strip()
    return f"••••{stripped[-4:]}" if stripped else ""


class ProviderService:
    """Owns credential handling and deterministic provider selection."""

    def __init__(self, repository: Repository, cipher: SecretCipher):
        self.repository = repository
        self.cipher = cipher

    def list_channels(self) -> list[dict[str, Any]]:
        return self.repository.list_provider_channels()

    def create_channel(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = str(payload.pop("api_key")).strip()
        payload["api_key_encrypted"] = self.cipher.encrypt(api_key)
        payload["api_key_hint"] = key_hint(api_key)
        return self.repository.create_provider_channel(**payload)

    def update_channel(
        self, channel_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        api_key = payload.pop("api_key", None)
        if api_key is not None:
            cleaned = str(api_key).strip()
            payload["api_key_encrypted"] = self.cipher.encrypt(cleaned)
            payload["api_key_hint"] = key_hint(cleaned)
        return self.repository.update_provider_channel(channel_id, payload)

    def _legacy_snapshot(
        self, *, requested_mode: str, quality: str, size: str
    ) -> dict[str, Any]:
        api_key = (
            os.getenv("IMAGE_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("api_key")
            or os.getenv("API_KEY")
            or ""
        )
        raw_cost = (
            os.getenv(f"IMAGE_COST_PER_IMAGE_{quality.upper()}_USD")
            or os.getenv("IMAGE_COST_PER_IMAGE_USD")
            or "0"
        )
        try:
            unit_price = max(0.0, float(raw_cost))
        except ValueError:
            unit_price = 0.0
        return {
            "channel_id": None,
            "channel_name": "本地环境兼容渠道",
            "base_url": os.getenv("IMAGE_API_BASE_URL") or os.getenv("base_url") or "",
            "endpoint": os.getenv("IMAGE_API_ENDPOINT", "/images/edits"),
            "api_key_encrypted": self.cipher.encrypt(api_key),
            "model": os.getenv("IMAGE_MODEL", "gpt-image-2"),
            "quality": quality,
            "size": size,
            "unit_price": unit_price,
            "currency": os.getenv("IMAGE_COST_CURRENCY", "USD"),
            "routing_mode": requested_mode,
            "source": "legacy_env",
        }

    def resolve_for_run(
        self,
        *,
        mode: str,
        channel_id: str | None,
        quality: str,
        size: str,
    ) -> dict[str, Any]:
        routing = self.repository.get_provider_routing()
        configured_channels = self.repository.list_provider_channels(include_secrets=True)
        if not configured_channels and mode == "default":
            return self._legacy_snapshot(
                requested_mode="legacy", quality=quality, size=size
            )

        resolved_mode = str(routing["mode"] if mode == "default" else mode)
        resolved_channel_id = channel_id
        if resolved_mode == "fixed" and not resolved_channel_id:
            resolved_channel_id = routing.get("fixed_channel_id")

        if resolved_mode == "fixed":
            selected = next(
                (
                    item
                    for item in configured_channels
                    if item["id"] == resolved_channel_id
                ),
                None,
            )
            if selected is None:
                raise ProviderConfigurationError("固定渠道不存在，请重新选择")
            if not selected["active"]:
                raise ProviderConfigurationError("固定渠道已停用，请重新选择")
        elif resolved_mode == "auto":
            currency = str(routing["currency"])
            eligible = [
                item
                for item in configured_channels
                if item["active"]
                and item["currency"] == currency
                and float(item["rates"].get(quality, 0)) > 0
            ]
            if not eligible:
                raise ProviderConfigurationError(
                    f"没有启用且以 {currency} 标注 {quality} 质量费率的渠道"
                )
            selected = min(
                eligible,
                key=lambda item: (
                    float(item["rates"][quality]),
                    str(item["name"]).casefold(),
                    str(item["id"]),
                ),
            )
        else:
            raise ProviderConfigurationError("未知的渠道路由模式")

        return {
            "channel_id": selected["id"],
            "channel_name": selected["name"],
            "base_url": selected["base_url"],
            "endpoint": selected["endpoint"],
            "api_key_encrypted": selected["api_key_encrypted"],
            "model": selected["model"],
            "quality": quality,
            "size": size,
            "unit_price": float(selected["rates"].get(quality, 0)),
            "currency": selected["currency"],
            "routing_mode": resolved_mode,
            "source": "managed",
        }

    @staticmethod
    def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in snapshot.items()
            if key != "api_key_encrypted"
        }

    def runtime_environment(self, job_id: str) -> dict[str, str]:
        snapshot = self.repository.get_generation_provider_snapshot(job_id)
        if snapshot is None:
            return {}
        api_key = self.cipher.decrypt(str(snapshot["api_key_encrypted"]))
        environment = {
            "IMAGE_API_BASE_URL": str(snapshot["base_url"]),
            "IMAGE_API_ENDPOINT": str(snapshot["endpoint"]),
            "IMAGE_API_KEY": api_key,
            "IMAGE_MODEL": str(snapshot["model"]),
            "IMAGE_SIZE": str(snapshot["size"]),
            "IMAGE_QUALITY": str(snapshot["quality"]),
            "IMAGE_COST_CURRENCY": str(snapshot["currency"]),
            "MUSEFORGE_PROVIDER_CHANNEL_ID": str(snapshot.get("channel_id") or "legacy"),
            "MUSEFORGE_PROVIDER_CHANNEL_NAME": str(snapshot["channel_name"]),
            "MUSEFORGE_PROVIDER_ROUTING_MODE": str(snapshot["routing_mode"]),
            "MUSEFORGE_PROVIDER_UNIT_PRICE": str(snapshot["unit_price"]),
        }
        return environment
