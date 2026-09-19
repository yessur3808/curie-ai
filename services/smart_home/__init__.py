"""Unified smart-home providers and orchestration for Curie.

Hub exports are loaded lazily so the provider-neutral entity resolver can
import canonical models and alias records without creating an import cycle.
"""

from .inventory import DeviceInventoryService, InventorySnapshot
from .models import CanonicalDevice, ControlReceipt, DeviceSnapshot, ProviderIssue


def __getattr__(name: str):
    if name in {"SmartHomeHub", "get_smart_home_hub", "reset_smart_home_hub"}:
        from .hub import SmartHomeHub, get_smart_home_hub, reset_smart_home_hub

        return {
            "SmartHomeHub": SmartHomeHub,
            "get_smart_home_hub": get_smart_home_hub,
            "reset_smart_home_hub": reset_smart_home_hub,
        }[name]
    raise AttributeError(name)


__all__ = [
    "CanonicalDevice",
    "ControlReceipt",
    "DeviceInventoryService",
    "DeviceSnapshot",
    "InventorySnapshot",
    "ProviderIssue",
    "SmartHomeHub",
    "get_smart_home_hub",
    "reset_smart_home_hub",
]
