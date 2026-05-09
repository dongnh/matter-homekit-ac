"""HAP accessory wrapping a single matter_webcontrol AC as a HeaterCooler."""
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


def _hk_target_to_matter(state: int) -> int:
    return {HK_AUTO: M_AUTO, HK_HEAT: M_HEAT, HK_COOL: M_COOL}[state]


class AcAccessory(Accessory):
    category = CATEGORY_AIR_CONDITIONER

    def __init__(self, driver, display_name: str, ac_id: str,
                 client: MatterClient, humidity_id: Optional[str] = None,
                 aid: Optional[int] = None):
        super().__init__(driver, display_name, aid=aid)
        self.ac_id = ac_id
        self.client = client
        self.humidity_id = humidity_id

        self.set_info_service(
            firmware_revision="0.1.0",
            manufacturer="dongnh",
            model="matter-homekit-ac",
            serial_number=ac_id,
        )

        chars = [
            "Active",
            "CurrentHeaterCoolerState",
            "TargetHeaterCoolerState",
            "CurrentTemperature",
            "CoolingThresholdTemperature",
        ]
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

        if humidity_id:
            svc.add_optional_characteristic("CurrentRelativeHumidity")
            self.cur_humidity = svc.configure_char(
                "CurrentRelativeHumidity", value=50.0,
            )
        else:
            self.cur_humidity = None

    # --- HAP setters: must return immediately, so HTTP runs on a worker thread.

    def _bg(self, fn, *args, **kwargs) -> None:
        threading.Thread(
            target=self._safe, args=(fn, *args), kwargs=kwargs, daemon=True,
        ).start()

    def _safe(self, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.warning("AC %s call %s failed: %s", self.ac_id, fn.__name__, e)

    def _set_active(self, value: int) -> None:
        self._bg(self.client.set_ac, self.ac_id, on=bool(value))

    def _set_target_state(self, value: int) -> None:
        self._bg(self.client.set_ac, self.ac_id, mode=_hk_target_to_matter(int(value)))

    def _set_cool_sp(self, value: float) -> None:
        self._bg(self.client.set_ac, self.ac_id, setpoint=float(value))

    # --- pulled by main loop poll thread.

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
        if on:
            self.target_state.set_value(_matter_to_hk_target(mode))
        if local_t is not None:
            self.cur_temp.set_value(float(local_t))
        if cool_sp is not None:
            self.cool_sp.set_value(float(cool_sp))

        if not on:
            self.cur_state.set_value(HK_INACTIVE)
        elif mode == M_HEAT:
            self.cur_state.set_value(HK_HEATING)
        elif mode in (M_COOL, M_AUTO):
            running = (
                local_t is not None
                and cool_sp is not None
                and float(local_t) > float(cool_sp)
            )
            self.cur_state.set_value(HK_COOLING if running else HK_IDLE)
        else:
            self.cur_state.set_value(HK_IDLE)

        if self.cur_humidity and self.humidity_id:
            try:
                clim = self.client.get_climate_one(self.humidity_id)
                if "humidity" in clim:
                    self.cur_humidity.set_value(float(clim["humidity"]))
            except Exception as e:
                log.debug("humidity %s poll failed: %s", self.humidity_id, e)


def build_bridge(driver, name: str, client: MatterClient,
                 accessories: list) -> Bridge:
    bridge = Bridge(driver, name)
    bridge.acs = []
    for cfg in accessories:
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
