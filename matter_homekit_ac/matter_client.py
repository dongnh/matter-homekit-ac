"""HTTP client for matter_webcontrol's REST API."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MatterClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _get(self, path: str, params: Optional[dict] = None):
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        req = Request(url, headers=self._headers())
        with urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, body: dict) -> dict:
        req = Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode(),
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def get_ac(self, ac_id: str) -> dict:
        return self._get("/api/ac", {"id": ac_id})

    def get_acs(self) -> list:
        return self._get("/api/acs")

    def get_climate_one(self, dev_id: str) -> dict:
        return self._get("/api/climate", {"id": dev_id})

    def set_ac(self, ac_id: str, on: Optional[bool] = None,
               mode: Optional[int] = None, setpoint: Optional[float] = None,
               fan_speed: Optional[int] = None) -> dict:
        body: dict = {"id": ac_id}
        if on is not None:
            body["on"] = on
        if mode is not None:
            body["mode"] = mode
        if setpoint is not None:
            body["setpoint"] = setpoint
        if fan_speed is not None:
            body["fan_speed"] = int(fan_speed)
        return self._post("/api/ac", body)
