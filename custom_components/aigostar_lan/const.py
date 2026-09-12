"""Constants for the Aigostar LAN integration."""

DOMAIN = "aigostar_lan"

# The embedded MQTT broker the bulbs connect to. The bulbs open TLS MQTT to
# public.iot-as-mqtt.eu-central-1.aliyuncs.com:1883, so a network redirect
# (DNS override or router DNAT) must point that hostname at the HA host, and the
# broker must listen on the same port the firmware uses.
CLOUD_MQTT_HOST = "public.iot-as-mqtt.eu-central-1.aliyuncs.com"
DEFAULT_PORT = 1883

CONF_PORT = "port"

# TSL properties for the Aigostar TG7100C data model (Alibaba Living Link).
PROP_SWITCH     = "LightSwitch"       # bool  0=off 1=on
PROP_BRIGHTNESS = "Brightness"        # int   1-100 (percentage)
PROP_COLOR_TEMP = "ColorTemperature"  # int   0-100 (0=warm 2700K, 100=cool 6500K)
PROP_LIGHT_MODE = "LightMode"         # enum  0=white 1=color(RGB)
PROP_HSV_COLOR  = "HSVColor"          # struct {Hue:0-360, Saturation:0-100, Value:0-100}

LIGHT_MODE_WHITE = 0
LIGHT_MODE_COLOR = 1

HSV_KEY_HUE        = "Hue"
HSV_KEY_SATURATION = "Saturation"
HSV_KEY_VALUE      = "Value"

AIGO_HUE_MAX = 360
AIGO_SAT_MAX = 100

# Kelvin <-> Aigostar percentage conversion.
KELVIN_WARM = 2700   # ColorTemperature = 0
KELVIN_COOL = 6500   # ColorTemperature = 100

# HA brightness 1-255 <-> Aigostar 1-100.
HA_BRIGHT_MAX   = 255
AIGO_BRIGHT_MIN = 1
AIGO_BRIGHT_MAX = 100

# Product name/key fragments that hint a colour-capable (RGBCCT) bulb.
COLOR_MODEL_HINTS = ("rgb", "color", "colour")

# Alink downstream/upstream topic templates (pk = ProductKey, dn = DeviceName).
TOPIC_PROPERTY_SET  = "/sys/{pk}/{dn}/thing/service/property/set"
TOPIC_PROPERTY_POST = "/sys/{pk}/{dn}/thing/event/property/post"
TOPIC_NTP_REQUEST   = "/ext/ntp/{pk}/{dn}/request"
TOPIC_NTP_RESPONSE  = "/ext/ntp/{pk}/{dn}/response"

# A device is considered offline if it has not spoken for this long.
DEVICE_TIMEOUT_SECONDS = 180
