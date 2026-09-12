"""Config flow for Aigostar LAN."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_PORT, DEFAULT_PORT, DOMAIN


class AigostarLanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance flow; only the broker port is configurable."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Aigostar LAN", data=user_input)

        schema = vol.Schema({vol.Required(CONF_PORT, default=DEFAULT_PORT): int})
        return self.async_show_form(step_id="user", data_schema=schema)
