# Matter HomeKit AC

A small HomeKit bridge that exposes air conditioners from a [Matter Web Controller](https://github.com/dongnh/matter_webcontrol) instance as native HomeKit accessories.

## Why

Vendor hubs that bridge IR air conditioners to Matter — including the Aqara M200 — do not forward their thermostat endpoints to Apple Home. The cluster is visible inside the hub's own fabric, but never appears as a tile in the Home app.

This bridge fills that gap, and only that gap.

## What it does

- Reads air conditioner state from `/api/acs` and `/api/climate`.
- Writes power, mode, and cooling setpoint back through `/api/ac`.
- Presents each AC as a single `HeaterCooler` tile, with the room's humidity attached.

## What it doesn't

- No discovery. No bridging of arbitrary Matter devices. No plugin system.
- Each accessory is declared explicitly in `config.json` — you choose the name, the room, the humidity sensor, the order.
- One responsibility, one bridge process. Lights and switches stay where they belong.

## Run

```sh
pip install -e .
matter-homekit-ac --config config.json
```

A minimal config:

```json
{
  "matter_url": "http://127.0.0.1:8080",
  "bridge_name": "Matter AC",
  "port": 51827,
  "state_path": "./accessory.state",
  "accessories": [
    {"ac_id": "dev_9a0cb3dd", "name": "Living Room", "humidity_id": "dev_3eb829b1"}
  ]
}
```

Pair the bridge in the Home app the first time it starts — the setup code is printed to the log.

## License

MIT.
