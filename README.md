# Aigostar Smart Lights — LAN control for Home Assistant

Control **Aigostar smart bulbs** (TG7100C / Bouffalo Lab chipset) entirely on your
local network — no Alibaba cloud, no internet dependency. Lower latency, and the
lights keep working when your internet goes down.

The bulbs normally talk to Alibaba Cloud IoT over MQTT. This integration runs a
small MQTT broker inside Home Assistant that the bulbs connect to instead, so
Home Assistant drives them directly over the LAN.

> **Companion to the cloud integration.** If you just want cloud control with no
> network changes, use [ha-aigostar](https://github.com/MarcoM1993/ha-aigostar).
> This project is for running the bulbs fully local.

## How it works

```
 bulb ──TLS/MQTT──►  Home Assistant (embedded broker)  ──►  light entity
                            ▲
            DNS: public.iot-as-mqtt.eu-central-1.aliyuncs.com ──► HA IP
```

The firmware does not validate the broker's TLS certificate, so the integration
presents a self-signed one and accepts the bulbs directly. Bulbs are
auto-discovered as they connect; each becomes a light entity with brightness,
colour temperature and (on RGBCCT models) colour.

## Requirements

- The bulbs must **already be paired to your Wi-Fi** (via the AigoSmart app).
  Onboarding brand-new bulbs without the app is not yet supported.
- Home Assistant must be able to bind **TCP port 1883** reachable from your LAN.
  On Home Assistant OS this works out of the box (host networking). On Container
  installs you must publish the port. Port 1883 must be free — if you run the
  Mosquitto add-on on 1883, pick another port here and redirect to it.
- Control over your **DNS** (a Pi-hole / AdGuard / router DNS override). This is
  the one manual step and it cannot be avoided — the hostname is hard-coded in
  the firmware.

## Setup

### 1. Install the integration

**HACS (custom repository):** HACS → ⋮ → Custom repositories → add this repo as
an *Integration* → install → restart Home Assistant.

**Manual:** copy `custom_components/aigostar_lan` into your HA
`config/custom_components/` directory and restart.

### 2. Add the integration

Settings → Devices & Services → **Add Integration** → *Aigostar Smart Lights
(LAN)*. Leave the port at **1883** unless you have a reason to change it. This
starts the local broker.

### 3. Redirect the bulbs to Home Assistant (DNS)

Point this hostname at your Home Assistant IP on your local DNS server:

```
public.iot-as-mqtt.eu-central-1.aliyuncs.com  →  <your HA IP>
```

- **Pi-hole:** Local DNS → DNS Records → add the name → HA IP.
- **AdGuard Home:** Filters → DNS rewrites → add the name → HA IP.
- **Router / dnsmasq:** `address=/public.iot-as-mqtt.eu-central-1.aliyuncs.com/<HA IP>`

> **This affects every Aigostar bulb on your network at once** — they all resolve
> the same hostname.

**Making the bulbs pick up the change.** A bulb caches the IP it resolved and, when
its connection drops, reconnects straight to that cached address without asking
DNS again — so simply restarting the connection is not enough. Either:

- **power-cycle the bulb** (a cold boot always re-resolves), or
- **briefly block its cloud path** so the cached address fails and it has to ask
  DNS again. On a Linux router, for each bulb:

  ```bash
  iptables -I FORWARD 1 -s <bulb IP> -p tcp --dport 1883 ! -d <HA IP> -j DROP
  # wait until the bulb appears in Home Assistant, then remove it:
  iptables -D FORWARD -s <bulb IP> -p tcp --dport 1883 ! -d <HA IP> -j DROP
  ```

Bulbs that are powered off at the time simply join on their own the next time
they are switched on.

### 4. (Optional) Go fully offline

Once redirected, a paired bulb works with **no internet at all** — it reconnects
using a token cached in its flash and never needs the cloud. If you want to
guarantee isolation, block the bulbs' WAN access at your firewall; local control
keeps working. NTP is answered locally over MQTT.

## Supported devices

| Device | Chipset | Status |
|--------|---------|--------|
| Aigostar RGBCCT bulb (E27/E14/GU10) | TG7100C (Bouffalo Lab BL602) | Tested |
| Aigostar white/CCT bulb | TG7100C | Should work (colour hidden automatically) |

## Limitations

- **Pairing is still done with the AigoSmart app** (Wi-Fi onboarding of a
  factory-new bulb without the app is not implemented yet).
- The DNS redirect is network-wide; there is no per-bulb switch.
- State is reported by the bulb after it applies a command; a bulb changed
  physically or from another controller updates on its next post.
- **A bulb switched off at the wall shows as `unavailable`,** but not instantly.
  Cutting power leaves its connection half-open — the bulb cannot announce that
  it is gone — so the integration waits for its MQTT keepalive to lapse and
  retires it after about three minutes. Switch it back on and it reconnects and
  reappears within seconds.
- **A bulb may start as `unknown`.** A bulb that has just been powered on
  reliably volunteers a full snapshot within a fraction of a second, so it
  shows up with the right state. A bulb that merely reconnects — after Home
  Assistant restarts, say — may send nothing. The integration also asks for the
  properties explicitly and retries, but no bulb has yet been observed
  answering that request, so it cannot be relied on. Rather than invent a
  value, the entity stays `unknown` until the bulb reports; the first command
  you send resolves it, and control is unaffected either way.

## Security

The integration accepts any client on its broker and presents a self-signed
certificate — this is safe because it only listens on your LAN and the bulbs do
not authenticate the server anyway. Do not expose port 1883 to the internet.

## Disclaimer

Unofficial and not affiliated with Aigostar. Built by reverse-engineering the
AigoSmart app and the bulbs' MQTT traffic, for personal and educational use. Use
at your own risk.

## License

[MIT](LICENSE)
