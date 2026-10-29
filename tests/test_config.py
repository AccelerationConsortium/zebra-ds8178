"""Connector configuration defaults and validation."""

import json
import pathlib

from unitelabs.zebra_ds8178.config import ZebraDs8178Config


def test_defaults_to_simulation_with_auto_detected_port() -> None:
    """A fresh config simulates and leaves the port to auto-detection."""
    config = ZebraDs8178Config.validate({})

    assert config.simulation_mode is True
    assert config.serial_port is None


def test_accepts_an_explicit_serial_port() -> None:
    """An explicitly configured serial port is kept."""
    config = ZebraDs8178Config.validate({"simulation_mode": False, "serial_port": "/dev/ttyACM0"})

    assert config.serial_port == "/dev/ttyACM0"


def test_server_type_names_the_instrument_class() -> None:
    """The SiLA server type is the instrument class, not the template's."""
    assert ZebraDs8178Config.validate({}).sila_server.type == "BarcodeReader"


def test_example_config_matches_the_defaults() -> None:
    """The example config in the repository shows every setting at its default."""
    example = json.loads((pathlib.Path(__file__).parents[1] / "config" / "zebra_ds8178_config.example.json").read_text())
    defaults = ZebraDs8178Config().to_dict()
    defaults["sila_server"].pop("uuid")  # left out of the example: every installation needs its own

    assert example == defaults
