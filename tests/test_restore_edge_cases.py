"""State round trips must preserve the order and meaning of device reports."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.light import ColorMode

from custom_components.aigostar_lan import AigostarLanManager
from custom_components.aigostar_lan.light import AigostarLanLight

from test_reported_state import DEVICE_NAME, PRODUCT_KEY, RGB_REPORT


class RestoreEdgeCaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.broker = SimpleNamespace(
            is_online=Mock(return_value=True), publish_set=AsyncMock()
        )
        self.manager = SimpleNamespace(broker=self.broker)

    def make_light(self) -> AigostarLanLight:
        return AigostarLanLight(self.manager, PRODUCT_KEY, DEVICE_NAME)

    async def restore(self, light: AigostarLanLight, previous: AigostarLanLight) -> None:
        with patch.object(
            light,
            "async_get_last_extra_data",
            AsyncMock(return_value=previous.extra_restore_state_data),
        ):
            await light.async_added_to_hass()

    async def test_partial_hsv_value_preserves_reported_colour_after_reload(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        previous.apply_state({"HSVColor": {"Value": 26}})
        self.assertEqual(previous.hs_color, (210, 80))
        self.assertEqual(previous.brightness, 64)

        restored = self.make_light()
        await self.restore(restored, previous)

        self.assertEqual(restored.hs_color, (210, 80))
        self.assertEqual(restored.brightness, 64)

    async def test_newer_brightness_report_is_not_replaced_by_older_hsv_value(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        previous.apply_state({"Brightness": 26})
        self.assertEqual(previous.brightness, 64)

        restored = self.make_light()
        await self.restore(restored, previous)

        self.assertEqual(restored.color_mode, ColorMode.HS)
        self.assertEqual(restored.brightness, 64)

    async def test_invalid_reports_do_not_erase_confirmed_values(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        previous.apply_state({
            "LightSwitch": None,
            "LightMode": "invalid",
            "Brightness": None,
            "ColorTemperature": "invalid",
            "HSVColor": {"Hue": None, "Saturation": "invalid", "Value": None},
        })

        restored = self.make_light()
        await self.restore(restored, previous)

        self.assertTrue(restored.is_on)
        self.assertEqual(restored.color_mode, ColorMode.HS)
        self.assertEqual(restored.hs_color, (210, 80))
        self.assertEqual(restored.brightness, 255)

    async def test_early_hsv_value_uses_restored_mode(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        restored = self.make_light()
        restored.apply_state({"HSVColor": {"Value": 26}})

        await self.restore(restored, previous)

        self.assertEqual(restored.color_mode, ColorMode.HS)
        self.assertEqual(restored.hs_color, (210, 80))
        self.assertEqual(restored.brightness, 64)

    async def test_early_mode_dependent_reports_keep_their_order(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        restored = self.make_light()
        # The value update applies while the restored state is RGB. Changing
        # mode later must not erase that already processed brightness report.
        restored.apply_state({"HSVColor": {"Value": 26}})
        restored.apply_state({"LightMode": 0})

        await self.restore(restored, previous)

        self.assertEqual(restored.color_mode, ColorMode.COLOR_TEMP)
        self.assertEqual(restored.brightness, 64)

    async def test_optimistic_mode_does_not_change_confirmed_hsv_interpretation(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        await previous.async_turn_on(color_temp_kelvin=2700, brightness=42)
        previous.apply_state({"HSVColor": {"Value": 26}})

        restored = self.make_light()
        await self.restore(restored, previous)

        self.assertTrue(restored.is_on)
        self.assertEqual(restored.color_mode, ColorMode.HS)
        self.assertEqual(restored.hs_color, (210, 80))
        self.assertEqual(restored.brightness, 64)

    async def test_warm_reconnect_requires_a_new_switch_confirmation(self) -> None:
        manager = AigostarLanManager(Mock(), Mock())
        manager.broker = self.broker
        added = []
        manager.bind_light_platform(added.extend)
        await manager._on_connect(PRODUCT_KEY, DEVICE_NAME)
        manager._on_state(PRODUCT_KEY, DEVICE_NAME, deepcopy(RGB_REPORT))
        light = added[0]
        self.assertFalse(light.assumed_state)

        # A replacement socket can CONNECT before the old socket is retired,
        # without an intervening unavailable callback from the broker.
        await manager._on_connect(PRODUCT_KEY, DEVICE_NAME)

        self.assertTrue(light.available)
        self.assertTrue(light.assumed_state)
        manager._on_state(PRODUCT_KEY, DEVICE_NAME, {"LightSwitch": 0})
        self.assertFalse(light.assumed_state)
        self.assertFalse(light.is_on)

    async def test_restore_does_not_reconfirm_a_previous_connection_report(self) -> None:
        previous = self.make_light()
        previous.apply_state(deepcopy(RGB_REPORT))
        restored = self.make_light()
        restored.apply_state({"LightSwitch": 0})
        restored.set_available(False)
        restored.set_available(True)

        await self.restore(restored, previous)

        self.assertFalse(restored.is_on)
        self.assertTrue(restored.assumed_state)

    async def test_aborted_entity_addition_stops_buffering_reports(self) -> None:
        light = self.make_light()
        light.apply_state(deepcopy(RGB_REPORT))

        # HA aborts addition for a disabled entity and runs its on-remove
        # callbacks. Device reports may still reach the manager afterwards.
        light.add_to_platform_abort()
        self.assertIsNone(light._pending_reports)
        light.apply_state({"LightSwitch": 0})
        self.assertIsNone(light._pending_reports)


if __name__ == "__main__":
    unittest.main()
