# Smart-home integrations

Curie exposes two chat tools over one normalized home model:

- `home_status` reads connectivity, power/running state, and available telemetry.
- `home_control` turns one unambiguously named device on or off, then reads its state again.

Examples:

```text
What's running at home?
Is the bedroom lamp on?
Turn off the desk plug
Switch the living-room Nanoleaf on
/home status
/home off Desk Plug
```

An exact single-device on/off request runs immediately. Pronouns, ambiguous names, offline devices, and whole-home bulk power requests fail safely and ask for a specific device. If `MASTER_USER_ID` is set, only that owner can view or control home devices.

## Install optional LAN adapters

The cloud HTTP adapters use Curie's core dependencies. Tapo, Mi Home, and LG ThinQ need their maintained Python clients:

```bash
./.venv/bin/pip install -r requirements-smart-home.txt
```

Restart Curie after updating `.env`.

## Provider setup

### SmartThings

Set `SMARTTHINGS_TOKEN` to a token with device read/status and command access. Curie uses the public SmartThings device, status, health, and command endpoints. A command response means the platform accepted the command, so Curie performs a follow-up status read.

### Govee

Set `GOVEE_API_KEY` to a Govee Developer API key. Curie uses the current `/router/api/v1` device list, state, and control endpoints.

### Nanoleaf

Pair on the local network and create an OpenAPI token, then configure one or more panels:

```dotenv
NANOLEAF_DEVICES_JSON=[{"name":"Living Room Panels","host":"192.168.1.50","token":"DEVICE_TOKEN"}]
```

Nanoleaf uses local HTTP on port 16021; the host is deliberately restricted to the private LAN.

### Tapo / TP-Link

Install the optional dependencies, provide the TP-Link account used to provision the devices, and list fixed LAN addresses:

```dotenv
TAPO_USERNAME=owner@example.com
TAPO_PASSWORD=private-password
TAPO_DEVICES_JSON=[{"name":"Desk Plug","host":"192.168.1.51"},{"name":"Bedroom Lamp","host":"lamp.local"}]
```

Curie uses `python-kasa` locally. That client is community maintained and not a TP-Link public consumer cloud API. Assign DHCP reservations so device addresses do not move.

### LG ThinQ air devices

Create a ThinQ Connect personal access token, generate one stable UUID for the client ID, and configure the account country:

```dotenv
LG_THINQ_PAT=personal-access-token
LG_THINQ_COUNTRY=US
LG_THINQ_CLIENT_ID=00000000-0000-4000-8000-000000000000
```

Curie filters the account to air conditioners, air purifiers, humidifiers, and dehumidifiers. Power control is sent only when the device profile exposes a writable air-conditioner operation mode.

### Mi Home / Xiaomi

Install the optional dependencies and configure each device's fixed LAN address and 32-character miIO token:

```dotenv
MI_HOME_DEVICES_JSON=[{"name":"Office Air Purifier","host":"192.168.1.52","token":"YOUR_32_CHARACTER_MIIO_TOKEN","model":"zhimi.airpurifier","properties":["power","temperature","humidity","aqi","filter_life_remaining"]}]
```

The default generic commands are `get_prop` and `set_power`. Devices with different firmware methods can override `status_method`, `set_power_method`, `power_property`, `on_params`, and `off_params` in the entry. `python-miio` is an unofficial local protocol implementation, so verify each model before relying on it for safety-critical automation.

### Petlibro

Petlibro does not publish a supported consumer developer API. Curie therefore integrates Petlibro entities through a local Home Assistant instance and its REST API. Install the Petlibro integration in Home Assistant, create a long-lived access token, then configure:

```dotenv
PETLIBRO_HA_URL=http://homeassistant.local:8123
PETLIBRO_HA_TOKEN=long-lived-home-assistant-token
PETLIBRO_ENTITIES_JSON=[{"name":"Cat Fountain Pump","entity_id":"switch.cat_fountain_pump","type":"fountain"},{"name":"Feeder Food Level","entity_id":"sensor.feeder_food_level","type":"feeder"}]
```

If `PETLIBRO_ENTITIES_JSON` is empty, Curie auto-detects Home Assistant entities whose IDs or attributes contain `petlibro`. Only switch-like entities expose on/off. Feeder actions such as dispensing food are intentionally not mapped to power commands.

## Encrypted per-owner credentials

Every provider also checks Curie's encrypted vault under `smart_home:<provider>` before falling back to environment values. Supported provider names are `smartthings`, `govee`, `nanoleaf`, `tapo`, `lg_thinq`, `petlibro`, and `mi_home`. The record fields mirror the environment configuration (`token`, `api_key`, `devices`, `pat`, `country`, `client_id`, `ha_url`, `ha_token`, and `entities`). The vault requires `CURIE_CREDENTIAL_KEY` and stores encrypted records with mode `0600`.

## Status analysis

Curie keeps connectivity separate from power state: an offline device is not assumed to be off. Summaries count on, off, offline, unknown, and actively running devices; include available temperature, humidity, air-quality, energy, brightness, battery, filter, water, and food metrics; and flag offline devices or consumables below 20 percent.

## API references

- [SmartThings API overview](https://developer.smartthings.com/docs/service-integrations/api-overview), [device queries](https://developer.smartthings.com/docs/service-integrations/query-and-list-devices), and [device commands](https://developer.smartthings.com/docs/service-integrations/control-devices)
- [Govee device list](https://developer.govee.com/reference/get-you-devices), [device state](https://developer.govee.com/reference/get-devices-status), and [device control](https://developer.govee.com/reference/control-you-devices)
- [Nanoleaf OpenAPI quick start](https://support.nanoleaf.me/hc/en-us/articles/41105798500628-API-Quick-Start-Guide)
- [LG ThinQ Connect Python SDK](https://github.com/thinq-connect/pythinqconnect)
- [python-kasa](https://github.com/python-kasa/python-kasa) and [python-miio](https://github.com/rytilahti/python-miio) community clients
- [Home Assistant REST API](https://developers.home-assistant.io/docs/api/rest/) and the [community Petlibro integration](https://github.com/jjjonesjr33/petlibro)
