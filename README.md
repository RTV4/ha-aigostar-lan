# Aigostar Smart Lights — LAN control for Home Assistant

<img src="https://raw.githubusercontent.com/RTV4/ha-aigostar-lan/main/custom_components/aigostar_lan/brand/icon@2x.png" alt="Aigostar LAN" width="128" align="right">

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

Add one DNS record on whatever resolver your bulbs actually use, pointing the
bulbs' hardcoded MQTT hostname at Home Assistant:

```
public.iot-as-mqtt.eu-central-1.aliyuncs.com  →  <your HA IP>
```

> Give Home Assistant a **fixed IP** first (a DHCP reservation, or a static
> address). This record points at it, so if HA's IP changes the bulbs break.

Recipes for the common resolvers — use the one you run:

- **Pi-hole** — Settings → *Local DNS Records* → add `public.iot-as-mqtt.eu-central-1.aliyuncs.com` → `<HA IP>`.
  (v6 stores it in `/etc/pihole/hosts/custom.list`; a line `<HA IP> public.iot-as-mqtt.eu-central-1.aliyuncs.com` + `pihole reloaddns` works too.)
- **AdGuard Home** — Filters → *DNS rewrites* → Add: domain `public.iot-as-mqtt.eu-central-1.aliyuncs.com`, answer `<HA IP>`.
- **OPNsense / pfSense (Unbound)** — Services → Unbound DNS → *Overrides* → Host Override: host `public`, domain `iot-as-mqtt.eu-central-1.aliyuncs.com`, IP `<HA IP>`. (Or a custom option: `local-data: "public.iot-as-mqtt.eu-central-1.aliyuncs.com A <HA IP>"`.)
- **OpenWrt / dnsmasq** — add to `/etc/dnsmasq.conf` (or a file in `/etc/dnsmasq.d/`): `address=/public.iot-as-mqtt.eu-central-1.aliyuncs.com/<HA IP>`, then restart dnsmasq.
- **MikroTik (RouterOS)** — `/ip dns static add name=public.iot-as-mqtt.eu-central-1.aliyuncs.com address=<HA IP>`.

> The override only takes effect if the bulbs actually query that resolver.
> Make sure your DHCP hands it out as the DNS server (or put the record on
> whatever resolver the bulbs really use).

**No DNS you can override? Redirect at the firewall instead.** On a Linux
router, DNAT the bulbs' MQTT to Home Assistant. Because the bulbs sit on the
same subnet as HA, you also need a hairpin `MASQUERADE` so replies come back
through the router — per bulb:

```bash
iptables -t nat -A PREROUTING  -s <bulb IP> -p tcp --dport 1883 -j DNAT --to-destination <HA IP>:1883
iptables -t nat -A POSTROUTING -s <bulb IP> -d <HA IP> -p tcp --dport 1883 -j MASQUERADE
```

> **This affects every Aigostar bulb on your network at once** — they all resolve
> the same hostname (the DNS route; the DNAT route is per-bulb).

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
- **A reconnecting bulb may not report its state.** The integration preserves
  the last device-reported properties across integration reloads and graceful
  Home Assistant restarts, including brightness, colour mode and RGB capability.
  Restored on/off state is marked as assumed until the bulb reports its switch
  state again. Availability still follows the live connection. Pending or failed
  commands are not saved as confirmed device state.
- **A bulb without a saved report may start as `unknown`.** The integration asks
  for the properties and retries, but no bulb has yet been observed answering
  that request. A new device therefore needs an actual report before there is
  anything to restore. Restoration does not query the physical state or send
  commands to the bulb. Home Assistant saves restore data periodically and on
  shutdown, so the latest report is not guaranteed to survive an abrupt power
  loss.

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
