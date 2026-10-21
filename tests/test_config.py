from unitelabs.zebra_ds8178.config import ZebraDs8178Config


def test_defaults_to_simulation_with_auto_detected_port():
    config = ZebraDs8178Config.validate({})

    assert config.simulation_mode is True
    assert config.serial_port is None


def test_accepts_an_explicit_serial_port():
    config = ZebraDs8178Config.validate({"simulation_mode": False, "serial_port": "/dev/ttyACM0"})

    assert config.serial_port == "/dev/ttyACM0"


def test_server_type_names_the_instrument_class():
    assert ZebraDs8178Config.validate({}).sila_server.type == "BarcodeReader"
