"""Entry point: load config, start HAP driver, poll matter_webcontrol."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from pyhap.accessory_driver import AccessoryDriver

from .accessory import build_bridge
from .matter_client import MatterClient

log = logging.getLogger(__name__)


def _stable_mac(seed: str) -> str:
    """Derive a stable MAC from the bridge name so accessory.state resets
    do not fragment the device into multiple stale identities in mDNS."""
    h = hashlib.sha256(seed.encode()).digest()
    first = (h[0] & 0xFE) | 0x02  # locally administered, unicast
    return ":".join(f"{b:02X}" for b in (first, h[1], h[2], h[3], h[4], h[5]))


def main() -> int:
    ap = argparse.ArgumentParser(prog="matter-homekit-ac")
    ap.add_argument("--config", required=True)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = json.loads(Path(args.config).read_text())

    client = MatterClient(cfg["matter_url"], api_key=cfg.get("api_key"))

    bridge_name = cfg.get("bridge_name", "Matter AC")
    port = int(cfg.get("port", 51827))
    state_path = cfg.get("state_path", "./accessory.state")
    poll_interval = float(cfg.get("poll_interval", 5.0))

    driver = AccessoryDriver(
        port=port,
        persist_file=state_path,
        mac=_stable_mac(bridge_name),
    )
    bridge = build_bridge(driver, bridge_name, client, cfg["accessories"])
    driver.add_accessory(accessory=bridge)

    stop = threading.Event()

    def poll_loop() -> None:
        while not stop.is_set():
            for acc in bridge.acs:
                acc.refresh()
            stop.wait(poll_interval)

    poller = threading.Thread(target=poll_loop, daemon=True, name="ac-poll")
    poller.start()

    def shutdown(*_):
        stop.set()
        driver.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    driver.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
