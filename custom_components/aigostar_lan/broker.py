"""Embedded async TLS MQTT broker the Aigostar bulbs connect to.

This is intentionally minimal: it accepts any client (the firmware signs its
own CONNECT, but there is no cloud to verify it against, so we simply accept),
learns each device's ProductKey/DeviceName from the CONNECT, forwards property
posts to Home Assistant, answers NTP-over-MQTT, and pushes property/set
commands. Commands are written straight to the device socket rather than routed
through a subscription table, because a warm-reconnecting bulb may never
re-SUBSCRIBE yet still applies pushed publishes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import mqtt_codec as codec
from .const import (
    TOPIC_NTP_RESPONSE,
    TOPIC_PROPERTY_POST,
    TOPIC_PROPERTY_SET,
)

_LOGGER = logging.getLogger(__name__)

ConnectCb = Callable[[str, str], Awaitable[None]]
StateCb = Callable[[str, str, dict], None]
AvailabilityCb = Callable[[str, str, bool], None]


class DeviceConn:
    """One connected bulb."""

    def __init__(self, pk: str, dn: str, writer: asyncio.StreamWriter) -> None:
        self.pk = pk
        self.dn = dn
        self.writer = writer
        self.last_seen = time.monotonic()
        self._cmd_id = 0

    def next_id(self) -> str:
        self._cmd_id += 1
        return str(self._cmd_id)


class AigostarBroker:
    """TLS MQTT listener + device registry."""

    def __init__(
        self,
        port: int,
        cert_path: Path,
        key_path: Path,
        on_connect: ConnectCb,
        on_state: StateCb,
        on_availability: AvailabilityCb,
    ) -> None:
        self._port = port
        self._cert_path = cert_path
        self._key_path = key_path
        self._on_connect = on_connect
        self._on_state = on_state
        self._on_availability = on_availability
        self._server: asyncio.AbstractServer | None = None
        self._devices: dict[tuple[str, str], DeviceConn] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _build_ssl(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self._cert_path, self._key_path)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # The firmware offers only legacy RSA/CBC suites; enable them.
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except ssl.SSLError:  # pragma: no cover - openssl build dependent
            _LOGGER.warning("Could not lower cipher security level; old bulbs may fail")
        return ctx

    async def start(self) -> None:
        # Building the SSL context reads the cert/key from disk, which is
        # blocking; keep it off the event loop.
        loop = asyncio.get_running_loop()
        ssl_ctx = await loop.run_in_executor(None, self._build_ssl)
        self._server = await asyncio.start_server(
            self._handle_client, "0.0.0.0", self._port, ssl=ssl_ctx
        )
        _LOGGER.info("Aigostar LAN broker listening on :%d", self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:  # pragma: no cover
                pass
        for conn in list(self._devices.values()):
            try:
                conn.writer.close()
            except Exception:  # pragma: no cover
                pass
        self._devices.clear()

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def is_online(self, pk: str, dn: str) -> bool:
        return (pk, dn) in self._devices

    async def publish_set(self, pk: str, dn: str, params: dict) -> None:
        """Send an alink thing.service.property.set to a device."""
        conn = self._devices.get((pk, dn))
        if conn is None:
            raise ConnectionError(f"device {pk}/{dn} not connected")
        payload = json.dumps(
            {
                "id": conn.next_id(),
                "version": "1.0.0",
                "method": "thing.service.property.set",
                "params": params,
            },
            separators=(",", ":"),
        ).encode()
        topic = TOPIC_PROPERTY_SET.format(pk=pk, dn=dn)
        conn.writer.write(codec.publish(topic, payload))
        await conn.writer.drain()
        _LOGGER.debug("Aigostar LAN -> %s/%s set %s", pk, dn, params)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        conn: DeviceConn | None = None
        try:
            while True:
                packet = await codec.read_packet(reader)
                if packet.ptype == codec.CONNECT:
                    conn = await self._on_connect_packet(packet, writer)
                elif conn is None:
                    continue  # ignore anything before CONNECT
                elif packet.ptype == codec.PUBLISH:
                    await self._on_publish(conn, packet)
                elif packet.ptype == codec.SUBSCRIBE:
                    pid, topics = codec.parse_subscribe_topics(packet.body)
                    writer.write(codec.suback(pid, len(topics)))
                    await writer.drain()
                elif packet.ptype == codec.PINGREQ:
                    writer.write(codec.pingresp())
                    await writer.drain()
                elif packet.ptype == codec.DISCONNECT:
                    break
                if conn is not None:
                    conn.last_seen = time.monotonic()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.debug("Aigostar LAN client %s error: %s", peer, err)
        finally:
            # Only retire the registry entry if it is still *this* connection.
            # A bulb that reconnects registers the new socket before the old
            # one finishes closing; without this check the dying connection
            # would evict its own replacement and the device would look
            # offline while actually being connected.
            if conn is not None and self._devices.get((conn.pk, conn.dn)) is conn:
                self._devices.pop((conn.pk, conn.dn), None)
                self._on_availability(conn.pk, conn.dn, False)
            try:
                writer.close()
            except Exception:  # pragma: no cover
                pass

    async def _on_connect_packet(
        self, packet: codec.Packet, writer: asyncio.StreamWriter
    ) -> DeviceConn | None:
        client_id = codec.parse_connect_client_id(packet.body)
        head = client_id.split("|", 1)[0]  # "{pk}.{dn}"
        if "." not in head:
            _LOGGER.warning("Aigostar LAN: unexpected clientId %r", client_id[:40])
            writer.write(codec.connack(False))
            await writer.drain()
            return None
        pk, dn = head.split(".", 1)
        # A reconnecting bulb leaves its previous socket half-open; drop it so
        # only one connection per device is ever live.
        previous = self._devices.get((pk, dn))
        if previous is not None and previous.writer is not writer:
            try:
                previous.writer.close()
            except Exception:  # pragma: no cover - best effort
                pass
        conn = DeviceConn(pk, dn, writer)
        self._devices[(pk, dn)] = conn
        writer.write(codec.connack(True))
        await writer.drain()
        _LOGGER.info("Aigostar LAN: %s/%s connected", pk, dn)
        await self._on_connect(pk, dn)
        self._on_availability(pk, dn, True)
        return conn

    async def _on_publish(self, conn: DeviceConn, packet: codec.Packet) -> None:
        topic, packet_id, payload = codec.parse_publish(packet.flags, packet.body)
        if packet_id is not None:
            conn.writer.write(codec.puback(packet_id))
            await conn.writer.drain()

        if topic.startswith("/ext/ntp/") and topic.endswith("/request"):
            await self._answer_ntp(conn, payload)
            return

        if topic == TOPIC_PROPERTY_POST.format(pk=conn.pk, dn=conn.dn):
            params = _extract_params(payload)
            if params:
                self._on_state(conn.pk, conn.dn, params)

    async def _answer_ntp(self, conn: DeviceConn, payload: bytes) -> None:
        try:
            device_send = json.loads(payload.split(b"\x00", 1)[0]).get("deviceSendTime")
        except (ValueError, AttributeError):
            device_send = None
        now = int(time.time() * 1000)
        body = json.dumps(
            {"deviceSendTime": device_send, "serverRecvTime": now, "serverSendTime": now}
        ).encode()
        topic = TOPIC_NTP_RESPONSE.format(pk=conn.pk, dn=conn.dn)
        conn.writer.write(codec.publish(topic, body))
        await conn.writer.drain()


def _extract_params(payload: bytes) -> dict:
    """Pull the alink `params` object out of a property post, tolerating junk."""
    try:
        data = json.loads(payload.split(b"\x00", 1)[0])
    except (ValueError, AttributeError):
        return {}
    params = data.get("params")
    if not isinstance(params, dict):
        return {}
    # The firmware sends either {"Brightness":50} or {"Brightness":{"value":50,"time":..}}.
    flat: dict = {}
    for key, value in params.items():
        if isinstance(value, dict) and "value" in value:
            flat[key] = value["value"]
        else:
            flat[key] = value
    return flat
