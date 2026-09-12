"""Just enough MQTT 3.1.1 to talk to the Aigostar firmware.

The bulbs are plain MQTT clients; the embedded broker only needs to accept them,
push property/set publishes, and read their event posts — not a full broker.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

# Control packet types (high nibble of byte 0).
CONNECT = 1
CONNACK = 2
PUBLISH = 3
PUBACK = 4
SUBSCRIBE = 8
SUBACK = 9
PINGREQ = 12
PINGRESP = 13
DISCONNECT = 14


@dataclass
class Packet:
    ptype: int
    flags: int
    body: bytes


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        digit = n % 128
        n //= 128
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


async def read_packet(reader: asyncio.StreamReader) -> Packet:
    """Read one MQTT packet; raises on EOF/malformed length."""
    header = await reader.readexactly(1)
    b0 = header[0]
    multiplier = 1
    length = 0
    while True:
        byte = (await reader.readexactly(1))[0]
        length += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            break
        multiplier *= 128
        if multiplier > 128 ** 3:
            raise ValueError("malformed remaining length")
    body = await reader.readexactly(length) if length else b""
    return Packet(b0 >> 4, b0 & 0x0F, body)


def connack(accepted: bool = True) -> bytes:
    return bytes([CONNACK << 4, 0x02, 0x00, 0x00 if accepted else 0x05])


def pingresp() -> bytes:
    return bytes([PINGRESP << 4, 0x00])


def puback(packet_id: int) -> bytes:
    return bytes([PUBACK << 4, 0x02]) + struct.pack("!H", packet_id)


def suback(packet_id: int, count: int) -> bytes:
    body = struct.pack("!H", packet_id) + bytes([0x00]) * count
    return bytes([SUBACK << 4]) + _encode_varint(len(body)) + body


def publish(topic: str, payload: bytes, qos: int = 0, packet_id: int = 1) -> bytes:
    topic_b = topic.encode()
    vh = struct.pack("!H", len(topic_b)) + topic_b
    if qos:
        vh += struct.pack("!H", packet_id)
    body = vh + payload
    return bytes([(PUBLISH << 4) | (qos << 1)]) + _encode_varint(len(body)) + body


def parse_connect_client_id(body: bytes) -> str:
    """Extract the clientId from a CONNECT body (protocol name, level, flags, keepalive)."""
    i = 2 + struct.unpack("!H", body[:2])[0]  # skip protocol name
    i += 4  # level(1) + connect flags(1) + keepalive(2)
    cid_len = struct.unpack("!H", body[i : i + 2])[0]
    return body[i + 2 : i + 2 + cid_len].decode(errors="replace")


def parse_subscribe_topics(body: bytes) -> tuple[int, list[str]]:
    """Return (packet_id, [topic filters]) from a SUBSCRIBE body."""
    packet_id = struct.unpack("!H", body[:2])[0]
    i = 2
    topics: list[str] = []
    while i < len(body):
        tlen = struct.unpack("!H", body[i : i + 2])[0]
        i += 2
        topics.append(body[i : i + tlen].decode(errors="replace"))
        i += tlen + 1  # + requested QoS byte
    return packet_id, topics


def parse_publish(flags: int, body: bytes) -> tuple[str, int | None, bytes]:
    """Return (topic, packet_id_or_None, payload) from a PUBLISH."""
    tlen = struct.unpack("!H", body[:2])[0]
    topic = body[2 : 2 + tlen].decode(errors="replace")
    i = 2 + tlen
    packet_id = None
    if flags & 0x06:  # QoS > 0
        packet_id = struct.unpack("!H", body[i : i + 2])[0]
        i += 2
    return topic, packet_id, body[i:]
