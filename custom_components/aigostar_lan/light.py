"""Light platform for Aigostar LAN — push-based, driven by the local broker."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    AIGO_BRIGHT_MAX,
    AIGO_BRIGHT_MIN,
    AIGO_HUE_MAX,
    AIGO_SAT_MAX,
    DOMAIN,
    HA_BRIGHT_MAX,
    HSV_KEY_HUE,
    HSV_KEY_SATURATION,
    HSV_KEY_VALUE,
    KELVIN_COOL,
    KELVIN_WARM,
    LIGHT_MODE_COLOR,
    LIGHT_MODE_WHITE,
    PROP_BRIGHTNESS,
    PROP_COLOR_TEMP,
    PROP_HSV_COLOR,
    PROP_LIGHT_MODE,
    PROP_SWITCH,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    manager = hass.data[DOMAIN][entry.entry_id]
    manager.bind_light_platform(async_add_entities)


class AigostarLanLight(LightEntity):
    """A single Aigostar bulb controlled over the LAN broker."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_min_color_temp_kelvin = KELVIN_WARM
    _attr_max_color_temp_kelvin = KELVIN_COOL

    def __init__(self, manager, pk: str, dn: str) -> None:
        self._manager = manager
        self._pk = pk
        self._dn = dn
        self._attr_unique_id = f"{DOMAIN}_{dn}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dn)},
            name=f"Aigostar {dn[-6:]}",
            manufacturer="Aigostar",
            model="TG7100C (LAN)",
        )
        # Stay unknown until the bulb reports. Claiming "off" before the first
        # state arrives is what made entities show a false off after a restart
        # or a reconnect that carried no snapshot.
        self._is_on: bool | None = None
        self._brightness: int | None = None
        self._color_temp_k = 4000
        self._color_mode = ColorMode.COLOR_TEMP
        self._hs_color: tuple[float, float] = (0.0, 0.0)
        self._attr_supported_color_modes = {ColorMode.COLOR_TEMP}
        self._attr_available = self._manager.broker.is_online(pk, dn)

    # ------------------------------------------------------------------
    # Conversions (kept identical to the cloud integration for parity)
    # ------------------------------------------------------------------

    @staticmethod
    def _aigo_to_ha_brightness(v: int) -> int:
        v = max(AIGO_BRIGHT_MIN, min(AIGO_BRIGHT_MAX, v))
        pct = (v - AIGO_BRIGHT_MIN) / (AIGO_BRIGHT_MAX - AIGO_BRIGHT_MIN)
        return max(1, round(pct * HA_BRIGHT_MAX))

    @staticmethod
    def _ha_to_aigo_brightness(v: int) -> int:
        pct = max(0, min(HA_BRIGHT_MAX, v)) / HA_BRIGHT_MAX
        return AIGO_BRIGHT_MIN + round(pct * (AIGO_BRIGHT_MAX - AIGO_BRIGHT_MIN))

    @staticmethod
    def _aigo_to_kelvin(v: int) -> int:
        return round(KELVIN_WARM + (v / 100.0) * (KELVIN_COOL - KELVIN_WARM))

    @staticmethod
    def _kelvin_to_aigo(k: int) -> int:
        pct = (k - KELVIN_WARM) / (KELVIN_COOL - KELVIN_WARM)
        return max(0, min(100, round(pct * 100)))

    # ------------------------------------------------------------------
    # HA properties
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        """None until the bulb has told us — HA then shows 'unknown', not 'off'."""
        return self._is_on

    @property
    def brightness(self) -> int | None:
        return self._brightness

    @property
    def color_mode(self) -> ColorMode:
        return self._color_mode

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._color_temp_k if self._color_mode == ColorMode.COLOR_TEMP else None

    @property
    def hs_color(self) -> tuple[float, float] | None:
        return self._hs_color if self._color_mode == ColorMode.HS else None

    @property
    def _supports_hs(self) -> bool:
        return ColorMode.HS in (self._attr_supported_color_modes or ())

    # ------------------------------------------------------------------
    # State from the device (property/post)
    # ------------------------------------------------------------------

    @callback
    def apply_state(self, params: dict) -> None:
        _LOGGER.debug("Aigostar LAN [%s] state: %s", self._dn, params)
        if PROP_HSV_COLOR in params or PROP_LIGHT_MODE in params:
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}

        if PROP_SWITCH in params:
            self._is_on = bool(_num(params[PROP_SWITCH], 0))

        if PROP_LIGHT_MODE in params:
            mode = _num(params[PROP_LIGHT_MODE], None)
            if mode is not None:
                self._color_mode = (
                    ColorMode.HS
                    if int(mode) == LIGHT_MODE_COLOR and self._supports_hs
                    else ColorMode.COLOR_TEMP
                )

        hsv = params.get(PROP_HSV_COLOR)
        hsv_value = None
        if isinstance(hsv, dict):
            hue = _num(hsv.get(HSV_KEY_HUE), None)
            sat = _num(hsv.get(HSV_KEY_SATURATION), None)
            if hue is not None and sat is not None:
                self._hs_color = (
                    max(0.0, min(float(AIGO_HUE_MAX), hue)),
                    max(0.0, min(float(AIGO_SAT_MAX), sat)),
                )
            hsv_value = _num(hsv.get(HSV_KEY_VALUE), None)

        if self._color_mode == ColorMode.HS and hsv_value is not None:
            self._brightness = self._aigo_to_ha_brightness(int(hsv_value))
        elif PROP_BRIGHTNESS in params:
            b = _num(params[PROP_BRIGHTNESS], None)
            if b is not None:
                self._brightness = self._aigo_to_ha_brightness(int(b))

        if PROP_COLOR_TEMP in params:
            ct = _num(params[PROP_COLOR_TEMP], None)
            if ct is not None:
                self._color_temp_k = self._aigo_to_kelvin(int(ct))

        if self.hass is not None:
            self.async_write_ha_state()

    @callback
    def set_available(self, available: bool) -> None:
        self._attr_available = available
        if self.hass is not None:
            self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        items: dict[str, Any] = {PROP_SWITCH: 1}
        req_brightness = kwargs.get(ATTR_BRIGHTNESS)
        req_hs = kwargs.get(ATTR_HS_COLOR)
        req_kelvin = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        ha_b = (
            int(req_brightness)
            if req_brightness is not None
            else (self._brightness if self._brightness is not None else HA_BRIGHT_MAX)
        )

        # A single LightMode: colour temp and colour are mutually exclusive;
        # colour temp wins when both are sent.
        if req_kelvin is not None:
            items[PROP_COLOR_TEMP] = self._kelvin_to_aigo(int(req_kelvin))
            items[PROP_LIGHT_MODE] = LIGHT_MODE_WHITE
            if req_brightness is not None:
                items[PROP_BRIGHTNESS] = self._ha_to_aigo_brightness(ha_b)
            self._color_temp_k = int(req_kelvin)
            self._color_mode = ColorMode.COLOR_TEMP
        elif req_hs is not None:
            hue, sat = req_hs
            items[PROP_HSV_COLOR] = self._hsv_item(float(hue), float(sat), ha_b)
            items[PROP_LIGHT_MODE] = LIGHT_MODE_COLOR
            self._hs_color = (float(hue), float(sat))
            self._color_mode = ColorMode.HS
        elif req_brightness is not None:
            if self._color_mode == ColorMode.HS and self._hs_color[1] > 0:
                items[PROP_HSV_COLOR] = self._hsv_item(*self._hs_color, ha_b)
                items[PROP_LIGHT_MODE] = LIGHT_MODE_COLOR
            else:
                items[PROP_BRIGHTNESS] = self._ha_to_aigo_brightness(ha_b)
        else:
            items[PROP_LIGHT_MODE] = (
                LIGHT_MODE_COLOR if self._color_mode == ColorMode.HS else LIGHT_MODE_WHITE
            )

        if req_brightness is not None:
            self._brightness = ha_b
        self._is_on = True
        await self._send(items)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        await self._send({PROP_SWITCH: 0})

    def _hsv_item(self, hue: float, sat: float, ha_b: int) -> dict[str, int]:
        return {
            HSV_KEY_HUE: max(0, min(AIGO_HUE_MAX, round(hue))),
            HSV_KEY_SATURATION: max(0, min(AIGO_SAT_MAX, round(sat))),
            HSV_KEY_VALUE: self._ha_to_aigo_brightness(ha_b),
        }

    async def _send(self, items: dict) -> None:
        try:
            await self._manager.broker.publish_set(self._pk, self._dn, items)
        except ConnectionError as err:
            _LOGGER.warning("Aigostar LAN [%s] command failed: %s", self._dn, err)
            self._attr_available = False
        if self.hass is not None:
            self.async_write_ha_state()


def _num(value: Any, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
