"""Exercise native HA entity removal and disk persistence without a real broker."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from homeassistant.components.light import ColorMode
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry, restore_state
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.aigostar_lan.light import AigostarLanLight


class RestoreLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        await self.hass.config_entries.async_initialize()
        device_registry.async_setup(self.hass)
        await device_registry.async_load(self.hass, load_empty=True)
        await entity_registry.async_load(self.hass, load_empty=True)
        await restore_state.async_get(self.hass).async_load()
        self.platform = EntityPlatform(
            hass=self.hass,
            logger=logging.getLogger(__name__),
            domain="light",
            platform_name="aigostar_lan",
            platform=None,
            scan_interval=timedelta(seconds=30),
            entity_namespace=None,
        )
        self.registry_entry = entity_registry.async_get(self.hass).async_get_or_create(
            "light", "aigostar_lan", "aigostar_lan_test-device",
            suggested_object_id="aigostar_test",
        )
        self.broker = SimpleNamespace(
            is_online=Mock(return_value=True), publish_set=AsyncMock()
        )
        self.manager = SimpleNamespace(broker=self.broker)
        self.report = {
            "LightSwitch": 1,
            "LightMode": 1,
            "HSVColor": {"Hue": 210, "Saturation": 80, "Value": 100},
        }
        self.added: list[AigostarLanLight] = []

    async def asyncTearDown(self) -> None:
        for light in list(self.added):
            await self.remove_light(light)
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def add_light(self, report: dict | None = None) -> AigostarLanLight:
        light = AigostarLanLight(self.manager, "test-product", "test-device")
        if report is not None:
            light.apply_state(deepcopy(report))
        light.entity_id = self.registry_entry.entity_id
        light.registry_entry = self.registry_entry
        # Run HA's real add/remove callbacks, including RestoreEntity's internal
        # hooks. No restore method or persistence helper is mocked.
        light.add_to_platform_start(self.hass, self.platform, None)
        await light.add_to_platform_finish()
        self.added.append(light)
        return light

    async def remove_light(self, light: AigostarLanLight) -> None:
        await light.async_remove()
        self.added.remove(light)

    def assert_restored_rgb(self, light: AigostarLanLight) -> None:
        state = self.hass.states.get(light.entity_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.state, "on")
        self.assertTrue(state.attributes["assumed_state"])
        self.assertEqual(state.attributes["color_mode"], ColorMode.HS)
        self.assertEqual(set(state.attributes["supported_color_modes"]),
                         {ColorMode.COLOR_TEMP, ColorMode.HS})
        self.assertEqual(state.attributes["hs_color"], (210, 80))
        self.assertEqual(state.attributes["brightness"], 255)

    async def test_entity_removal_restores_rgb_without_a_new_report(self) -> None:
        previous = await self.add_light(self.report)
        self.assertEqual(self.hass.states.get(previous.entity_id).state, "on")

        await self.remove_light(previous)
        light = await self.add_light()

        self.assert_restored_rgb(light)
        self.broker.publish_set.assert_not_awaited()
        light.apply_state({"LightSwitch": 0})
        state = self.hass.states.get(light.entity_id)
        self.assertEqual(state.state, "off")
        self.assertFalse(light.assumed_state)

    async def test_disk_round_trip_keeps_report_when_visible_state_is_unavailable(self) -> None:
        previous = await self.add_light(self.report)
        # The displayed command intent differs from the last actual report.
        await previous.async_turn_off()
        previous.set_available(False)
        self.assertEqual(self.hass.states.get(previous.entity_id).state, "unavailable")
        await self.remove_light(previous)

        await restore_state.async_get(self.hass).async_dump_states()
        self.assertTrue(
            Path(self.directory.name, ".storage", "core.restore_state").is_file()
        )
        self.hass.data.pop(restore_state.DATA_RESTORE_STATE)
        await restore_state.async_get(self.hass).async_load()
        light = await self.add_light()

        self.assert_restored_rgb(light)


if __name__ == "__main__":
    unittest.main()
