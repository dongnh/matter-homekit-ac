"""HAP accessories wrapping matter_webcontrol AC / heater devices.

Two accessory shapes are supported, selected by config `kind`:

  - kind="ac"      → cooling-only HeaterCooler (Cool/Auto, CoolingThreshold)
  - kind="heater"  → heating-only HeaterCooler (Heat, HeatingThreshold,
                     optional RotationSpeed mapped to /api/ac fan_speed)
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from pyhap.accessory import Accessory, Bridge
from pyhap.const import CATEGORY_AIR_CONDITIONER

from .matter_client import MatterClient

log = logging.getLogger(__name__)

# Matter Thermostat cluster (513) SystemMode values used by matter_webcontrol.
M_OFF, M_AUTO, M_COOL, M_HEAT = 0, 1, 3, 4

# HomeKit HeaterCooler enumerations.
HK_INACTIVE, HK_IDLE, HK_HEATING, HK_COOLING = 0, 1, 2, 3
HK_AUTO, HK_HEAT, HK_COOL = 0, 1, 2


def _matter_to_hk_target(mode: int) -> int:
    return {M_AUTO: HK_AUTO, M_HEAT: HK_HEAT, M_COOL: HK_COOL}.get(mode, HK_COOL)


class _BaseAccessory(Accessory):
    """Shared scaffolding for AC + Heater accessories."""

    def __init__(self, driver, display_name: str, ac_id: str,
                 client: MatterClient, humidity_id: Optional[str] = None,
                 aid: Optional[int] = None, model: str = "matter-homekit-ac"):
        super().__init__(driver, display_name, aid=aid)
        self.ac_id = ac_id
        self.client = client
        self.humidity_id = humidity_id

        self.set_info_service(
            firmware_revision="0.2.0",
            manufacturer="dongnh",
            model=model,
            serial_number=ac_id,
        )

    def _bg(self, fn, *args, **kwargs) -> None:
        threading.Thread(
            target=self._safe, args=(fn, *args), kwargs=kwargs, daemon=True,
        ).start()

    def _safe(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.warning("AC %s call %s failed: %s", self.ac_id, fn.__name__, e)

    def _poll_humidity(self) -> None:
        if not (getattr(self, "cur_humidity", None) and self.humidity_id):
            return
        try:
            clim = self.client.get_climate_one(self.humidity_id)
            if "humidity" in clim:
                self.cur_humidity.set_value(float(clim["humidity"]))
        except Exception as e:
            log.debug("humidity %s poll failed: %s", self.humidity_id, e)


class AcAccessory(_BaseAccessory):
    """Cooling-only AC. Exposes Auto + Cool target states."""

    category = CATEGORY_AIR_CONDITIONER

    def __init__(self, driver, display_name: str, ac_id: str,
                 client: MatterClient, humidity_id: Optional[str] = None,
                 aid: Optional[int] = None):
        super().__init__(driver, display_name, ac_id, client, humidity_id, aid)

        chars = [
            "Active",
            "CurrentHeaterCoolerState",
            "TargetHeaterCoolerState",
            "CurrentTemperature",
            "CoolingThresholdTemperature",
        ]
        if humidity_id:
            chars.append("CurrentRelativeHumidity")
        svc = self.add_preload_service("HeaterCooler", chars=chars)

        self.active = svc.configure_char(
            "Active", value=0, setter_callback=self._set_active,
        )
        self.target_state = svc.configure_char(
            "TargetHeaterCoolerState",
            value=HK_COOL,
            valid_values={"Auto": HK_AUTO, "Cool": HK_COOL},
            setter_callback=self._set_target_state,
        )
        self.cool_sp = svc.configure_char(
            "CoolingThresholdTemperature",
            value=25.0,
            properties={"minValue": 16.0, "maxValue": 30.0, "minStep": 0.5},
            setter_callback=self._set_cool_sp,
        )
        self.cur_temp = svc.configure_char("CurrentTemperature", value=25.0)
        self.cur_state = svc.configure_char(
            "CurrentHeaterCoolerState", value=HK_INACTIVE,
        )
        self.cur_humidity = (
            svc.configure_char("CurrentRelativeHumidity", value=50.0)
            if humidity_id else None
        )

    def _set_active(self, value: int) -> None:
        self._bg(self.client.set_ac, self.ac_id, on=bool(value))

    def _set_target_state(self, value: int) -> None:
        mode = {HK_AUTO: M_AUTO, HK_COOL: M_COOL}.get(int(value), M_COOL)
        self._bg(self.client.set_ac, self.ac_id, mode=mode)

    def _set_cool_sp(self, value: float) -> None:
        self._bg(self.client.set_ac, self.ac_id, setpoint=float(value))

    def refresh(self) -> None:
        try:
            ac = self.client.get_ac(self.ac_id)
        except Exception as e:
            log.warning("AC %s poll failed: %s", self.ac_id, e)
            return

        mode = int(ac.get("system_mode", 0))
        on = bool(ac.get("on", False))
        local_t = ac.get("local_temperature")
        cool_sp = ac.get("cooling_setpoint")

        self.active.set_value(1 if on else 0)
        if on and mode in (M_AUTO, M_COOL):
            self.target_state.set_value(_matter_to_hk_target(mode))
        if local_t is not None:
            self.cur_temp.set_value(float(local_t))
        if cool_sp is not None:
            self.cool_sp.set_value(float(cool_sp))

        if not on:
            self.cur_state.set_value(HK_INACTIVE)
        elif mode in (M_COOL, M_AUTO):
            running = (
                local_t is not None and cool_sp is not None
                and float(local_t) > float(cool_sp)
            )
            self.cur_state.set_value(HK_COOLING if running else HK_IDLE)
        else:
            self.cur_state.set_value(HK_IDLE)

        self._poll_humidity()


class HeaterAccessory(_BaseAccessory):
    """Heating-only HeaterCooler. Exposes Heat target + HeatingThreshold,
    plus optional RotationSpeed wired to matter_webcontrol's fan_speed.

    Uses CATEGORY_AIR_CONDITIONER (not CATEGORY_HEATER) because Apple Home
    silently disables room reassignment on bridged accessories whose category
    differs from the parent bridge's domain. Service is still HeaterCooler so
    functionality is unchanged; only the tile icon hint differs.
    """

    category = CATEGORY_AIR_CONDITIONER

    def __init__(self, driver, display_name: str, ac_id: str,
                 client: MatterClient, humidity_id: Optional[str] = None,
                 has_fan: bool = True, aid: Optional[int] = None,
                 heating_range: tuple[float, float] = (16.0, 30.0)):
        super().__init__(
            driver, display_name, ac_id, client, humidity_id, aid,
            model="matter-homekit-heater",
        )
        self.has_fan = has_fan

        chars = [
            "Active",
            "CurrentHeaterCoolerState",
            "TargetHeaterCoolerState",
            "CurrentTemperature",
            "HeatingThresholdTemperature",
        ]
        if has_fan:
            chars.append("RotationSpeed")
        if humidity_id:
            chars.append("CurrentRelativeHumidity")
        svc = self.add_preload_service("HeaterCooler", chars=chars)

        self.active = svc.configure_char(
            "Active", value=0, setter_callback=self._set_active,
        )
        # Apple Home disables some UI controls (incl. room reassignment on
        # bridged accessories) when valid_values contains only one entry.
        # Expose Auto + Heat — the setter coerces everything to Heat anyway.
        self.target_state = svc.configure_char(
            "TargetHeaterCoolerState",
            value=HK_HEAT,
            valid_values={"Auto": HK_AUTO, "Heat": HK_HEAT},
            setter_callback=self._set_target_state,
        )
        self.heat_sp = svc.configure_char(
            "HeatingThresholdTemperature",
            value=heating_range[0],
            properties={"minValue": heating_range[0],
                        "maxValue": heating_range[1], "minStep": 0.5},
            setter_callback=self._set_heat_sp,
        )
        self.cur_temp = svc.configure_char("CurrentTemperature", value=20.0)
        self.cur_state = svc.configure_char(
            "CurrentHeaterCoolerState", value=HK_INACTIVE,
        )
        self.fan_speed = (
            svc.configure_char(
                "RotationSpeed",
                value=0,
                properties={"minValue": 0, "maxValue": 100, "minStep": 1},
                setter_callback=self._set_fan_speed,
            )
            if has_fan else None
        )
        self.cur_humidity = (
            svc.configure_char("CurrentRelativeHumidity", value=50.0)
            if humidity_id else None
        )

    def _set_active(self, value: int) -> None:
        # On a heater the only meaningful mode is Heat; off is via Active=0.
        self._bg(
            self.client.set_ac, self.ac_id,
            on=bool(value), mode=M_HEAT if value else None,
        )

    def _set_target_state(self, value: int) -> None:
        # Only "Heat" is valid — make explicit anyway in case Apple Home retries.
        self._bg(self.client.set_ac, self.ac_id, mode=M_HEAT)

    def _set_heat_sp(self, value: float) -> None:
        self._bg(self.client.set_ac, self.ac_id, setpoint=float(value))

    def _set_fan_speed(self, value: int) -> None:
        self._bg(self.client.set_ac, self.ac_id, fan_speed=int(value))

    def refresh(self) -> None:
        try:
            ac = self.client.get_ac(self.ac_id)
        except Exception as e:
            log.warning("Heater %s poll failed: %s", self.ac_id, e)
            return

        mode = int(ac.get("system_mode", 0))
        on = bool(ac.get("on", False))
        local_t = ac.get("local_temperature")
        heat_sp = ac.get("heating_setpoint")
        fan = ac.get("fan_speed")

        self.active.set_value(1 if on else 0)
        # Target state is Heat regardless — heater has no other mode.
        self.target_state.set_value(HK_HEAT)
        if local_t is not None:
            self.cur_temp.set_value(float(local_t))
        if heat_sp is not None:
            self.heat_sp.set_value(float(heat_sp))
        if self.fan_speed is not None and fan is not None:
            self.fan_speed.set_value(int(fan))

        if not on:
            self.cur_state.set_value(HK_INACTIVE)
        elif mode == M_HEAT:
            running = (
                local_t is not None and heat_sp is not None
                and float(local_t) < float(heat_sp)
            )
            self.cur_state.set_value(HK_HEATING if running else HK_IDLE)
        else:
            self.cur_state.set_value(HK_IDLE)

        self._poll_humidity()


def build_bridge(driver, name: str, client: MatterClient,
                 accessories: list) -> Bridge:
    bridge = Bridge(driver, name)
    bridge.acs = []
    for cfg in accessories:
        kind = (cfg.get("kind") or "ac").lower()
        if kind == "heater":
            heating_range = tuple(cfg.get("heating_range", (16.0, 30.0)))
            acc = HeaterAccessory(
                driver,
                cfg["name"],
                cfg["ac_id"],
                client,
                humidity_id=cfg.get("humidity_id"),
                has_fan=bool(cfg.get("fan", True)),
                heating_range=heating_range,
            )
        else:
            acc = AcAccessory(
                driver,
                cfg["name"],
                cfg["ac_id"],
                client,
                humidity_id=cfg.get("humidity_id"),
            )
        bridge.add_accessory(acc)
        bridge.acs.append(acc)
    return bridge
