"""Aigostar LAN — control Aigostar bulbs locally, with no Alibaba cloud.

An embedded TLS MQTT broker accepts the bulbs (their firmware does not validate
the server certificate), so redirecting the Alibaba MQTT hostname to this host
lets Home Assistant drive them directly over the LAN.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .broker import AigostarBroker
from .cert import ensure_cert
from .const import CONF_PORT, DEFAULT_PORT, DOMAIN
from .light import AigostarLanLight

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LIGHT]


class AigostarLanManager:
    """Ties the broker's device events to Home Assistant light entities."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.broker: AigostarBroker | None = None
        self._entities: dict[str, AigostarLanLight] = {}
        self._add_entities: AddEntitiesCallback | None = None
        self._pending: list[tuple[str, str]] = []

    async def async_start(self) -> None:
        store = Path(self.hass.config.path(DOMAIN))
        store.mkdir(parents=True, exist_ok=True)
        cert_path, key_path = await self.hass.async_add_executor_job(ensure_cert, store)
        port = self.entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.broker = AigostarBroker(
            port, cert_path, key_path,
            on_connect=self._on_connect,
            on_state=self._on_state,
            on_availability=self._on_availability,
        )
        await self.broker.start()

    async def async_stop(self) -> None:
        if self.broker is not None:
            await self.broker.stop()

    @callback
    def bind_light_platform(self, add_entities: AddEntitiesCallback) -> None:
        self._add_entities = add_entities
        pending, self._pending = self._pending, []
        for pk, dn in pending:
            self._create_entity(pk, dn)

    # --- broker callbacks (run on the event loop) ---------------------

    async def _on_connect(self, pk: str, dn: str) -> None:
        if dn in self._entities:
            self._entities[dn].set_available(True)
            return
        if self._add_entities is None:
            self._pending.append((pk, dn))
            return
        self._create_entity(pk, dn)

    @callback
    def _create_entity(self, pk: str, dn: str) -> None:
        if dn in self._entities or self._add_entities is None:
            return
        entity = AigostarLanLight(self, pk, dn)
        self._entities[dn] = entity
        self._add_entities([entity])
        _LOGGER.info("Aigostar LAN: added light for %s/%s", pk, dn)

    @callback
    def _on_state(self, pk: str, dn: str, params: dict) -> None:
        entity = self._entities.get(dn)
        if entity is not None:
            entity.apply_state(params)

    @callback
    def _on_availability(self, pk: str, dn: str, available: bool) -> None:
        entity = self._entities.get(dn)
        if entity is not None:
            entity.set_available(available)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    manager = AigostarLanManager(hass, entry)
    try:
        await manager.async_start()
    except OSError as err:
        _LOGGER.error("Aigostar LAN: could not start broker on port: %s", err)
        raise
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    manager: AigostarLanManager = hass.data[DOMAIN].get(entry.entry_id)
    if manager is not None:
        await manager.async_stop()
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
