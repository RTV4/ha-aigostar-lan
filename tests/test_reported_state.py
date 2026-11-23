"""Regression tests for reloads, early reports, and restored light capabilities.

Run with the real Home Assistant package installed:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.light import ColorMode
from homeassistant.helpers.restore_state import RestoredExtraData

from custom_components.aigostar_lan import AigostarLanManager
from custom_components.aigostar_lan.light import AigostarLanLight


PRODUCT_KEY = "test-product"
DEVICE_NAME = "test-device"
RGB_REPORT = {
    "LightSwitch": 1,
    "LightMode": 1,
    "Brightness": 23,
    "ColorTemperature": 30,
    "HSVColor": {"Hue": 210, "Saturation": 80, "Value": 100},
}


class ReportedStateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = SimpleNamespace(
            is_online=Mock(return_value=True), publish_set=AsyncMock()
        )
        self.manager = SimpleNamespace(broker=self.broker)

    def make_light(self) -> AigostarLanLight:
        return AigostarLanLight(self.manager, PRODUCT_KEY, DEVICE_NAME)

    def saved_report(self, report: dict | None = None) -> RestoredExtraData:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT if report is None else report))
        # Exercise the JSON-compatible data that HA saves across a restart.
        return RestoredExtraData(
            json.loads(json.dumps(previous.extra_restore_state_data.as_dict()))
        )

    async def restore(self, light: AigostarLanLight, saved) -> None:
        with patch.object(
            light, "async_get_last_extra_data", AsyncMock(return_value=saved)
        ):
            await light.async_added_to_hass()

    def assert_rgb(self, light: AigostarLanLight) -> None:
        self.assertEqual(light.supported_color_modes, {ColorMode.COLOR_TEMP, ColorMode.HS})
        self.assertEqual(light.color_mode, ColorMode.HS)
        self.assertEqual(light.hs_color, (210, 80))
        self.assertEqual(light.brightness, 255)

    async def test_report_before_platform_binding_is_retained(self) -> None:
        manager = AigostarLanManager(Mock(), Mock())
        manager.broker = self.broker
        added = []

        await manager._on_connect(PRODUCT_KEY, DEVICE_NAME)
        manager._on_state(PRODUCT_KEY, DEVICE_NAME, deepcopy(RGB_REPORT))
        manager.bind_light_platform(added.extend)

        self.assertEqual(len(added), 1)
        light = added[0]
        self.assertTrue(light.is_on)
        self.assert_rgb(light)
        self.assertFalse(light.assumed_state)
        # A reconnect updates the same entity instead of adding a duplicate.
        await manager._on_connect(PRODUCT_KEY, DEVICE_NAME)
        self.assertEqual(len(added), 1)

    async def test_disconnect_before_binding_preserves_report_but_not_availability(self) -> None:
        manager = AigostarLanManager(Mock(), Mock())
        manager.broker = self.broker
        added = []
        await manager._on_connect(PRODUCT_KEY, DEVICE_NAME)
        manager._on_state(PRODUCT_KEY, DEVICE_NAME, deepcopy(RGB_REPORT))
        manager._on_availability(PRODUCT_KEY, DEVICE_NAME, False)
        manager.bind_light_platform(added.extend)

        self.assertEqual(len(added), 1)
        self.assertFalse(added[0].available)
        self.assertTrue(added[0].assumed_state)
        self.assert_rgb(added[0])

    async def test_on_and_off_rgb_reports_restore_as_assumed(self) -> None:
        for is_on in (0, 1):
            with self.subTest(is_on=is_on):
                saved = self.saved_report({**RGB_REPORT, "LightSwitch": is_on})
                light = self.make_light()
                await self.restore(light, saved)

                self.assertEqual(light.is_on, bool(is_on))
                self.assertTrue(light.assumed_state)
                self.assertTrue(light.available)
                self.assert_rgb(light)

    async def test_live_report_before_restore_wins(self) -> None:
        light = self.make_light()
        light.apply_state({"LightSwitch": 0, "LightMode": 0, "Brightness": 100})

        await self.restore(light, self.saved_report())

        self.assertFalse(light.is_on)
        self.assertFalse(light.assumed_state)
        self.assertEqual(light.color_mode, ColorMode.COLOR_TEMP)
        self.assertEqual(light.brightness, 255)
        self.assertIn(ColorMode.HS, light.supported_color_modes)

    async def test_live_report_during_restore_lookup_wins(self) -> None:
        light = self.make_light()
        started = asyncio.Event()
        release = asyncio.Event()
        saved = self.saved_report()

        async def load_saved():
            started.set()
            await release.wait()
            return saved

        with patch.object(light, "async_get_last_extra_data", load_saved):
            restoring = asyncio.create_task(light.async_added_to_hass())
            await started.wait()
            light.apply_state({"LightSwitch": 0})
            release.set()
            await restoring

        self.assertFalse(light.is_on)
        self.assertFalse(light.assumed_state)
        self.assert_rgb(light)

    async def test_partial_live_report_keeps_other_cached_properties(self) -> None:
        light = self.make_light()
        light.apply_state({"ColorTemperature": 100})

        await self.restore(light, self.saved_report({**RGB_REPORT, "LightMode": 0}))

        self.assertTrue(light.is_on)
        self.assertTrue(light.assumed_state)
        self.assertEqual(light.color_temp_kelvin, 6500)
        self.assertIn(ColorMode.HS, light.supported_color_modes)

    async def test_only_fresh_switch_report_confirms_restored_switch(self) -> None:
        light = self.make_light()
        await self.restore(light, self.saved_report())
        light.apply_state({"Brightness": 100})
        self.assertTrue(light.assumed_state)

        light.apply_state({"LightSwitch": 0})
        self.assertFalse(light.assumed_state)
        self.assertFalse(light.is_on)

    async def test_disconnect_and_reconnect_wait_for_fresh_switch_confirmation(self) -> None:
        light = self.make_light()
        light.apply_state(deepcopy(RGB_REPORT))
        light.set_available(False)
        self.assertFalse(light.available)
        self.assertTrue(light.assumed_state)

        light.set_available(True)
        self.assertTrue(light.available)
        self.assertTrue(light.assumed_state)
        light.apply_state({"LightSwitch": 1})
        self.assertFalse(light.assumed_state)

    async def test_unavailable_light_keeps_restorable_rgb_state(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        previous.set_available(False)
        saved = previous.extra_restore_state_data
        self.broker.is_online.return_value = False
        light = self.make_light()

        await self.restore(light, saved)

        self.assertFalse(light.available)
        self.assertTrue(light.assumed_state)
        self.assertTrue(light.is_on)
        self.assert_rgb(light)

    async def test_optimistic_commands_do_not_change_saved_report(self) -> None:
        light = self.make_light()
        light.apply_state(deepcopy(RGB_REPORT))
        saved = light.extra_restore_state_data.as_dict()

        await light.async_turn_off()
        await light.async_turn_on(brightness=42, color_temp_kelvin=2700)

        self.assertEqual(light.extra_restore_state_data.as_dict(), saved)
        restored = self.make_light()
        await self.restore(restored, light.extra_restore_state_data)
        self.assertTrue(restored.is_on)
        self.assert_rgb(restored)

    async def test_failed_command_does_not_replace_last_report(self) -> None:
        light = self.make_light()
        light.apply_state(deepcopy(RGB_REPORT))
        saved = light.extra_restore_state_data.as_dict()
        self.broker.publish_set.side_effect = ConnectionError("test socket failure")

        with self.assertLogs("custom_components.aigostar_lan.light", level="WARNING"):
            await light.async_turn_off()

        self.assertFalse(light.available)
        self.assertEqual(light.extra_restore_state_data.as_dict(), saved)

    async def test_unreported_command_does_not_create_restorable_state(self) -> None:
        light = self.make_light()
        await light.async_turn_on()
        restored = self.make_light()

        await self.restore(restored, light.extra_restore_state_data)

        self.assertIsNone(restored.is_on)
        self.assertIsNone(restored.brightness)

    async def test_new_device_without_restore_data_stays_unknown(self) -> None:
        light = self.make_light()
        await self.restore(light, None)

        self.assertIsNone(light.is_on)
        self.assertIsNone(light.brightness)
        self.assertEqual(light.supported_color_modes, {ColorMode.COLOR_TEMP})

    async def test_plain_turn_on_does_not_force_a_colour_mode(self) -> None:
        for report in (None, RGB_REPORT, {"LightSwitch": 0, "LightMode": 0}):
            with self.subTest(report=report):
                light = self.make_light()
                if report is not None:
                    light.apply_state(deepcopy(report))
                self.broker.publish_set.reset_mock()

                await light.async_turn_on()

                self.broker.publish_set.assert_awaited_once_with(
                    PRODUCT_KEY, DEVICE_NAME, {"LightSwitch": 1}
                )

    async def test_restore_snapshot_is_not_mutated_by_later_reports(self) -> None:
        light = self.make_light()
        light.apply_state(deepcopy(RGB_REPORT))
        saved = light.extra_restore_state_data
        light.apply_state({"LightSwitch": 0})

        restored = self.make_light()
        await self.restore(restored, saved)

        self.assertTrue(restored.is_on)
        self.assert_rgb(restored)


if __name__ == "__main__":
    unittest.main()
