"""Sounding the beeper through the SiLA feature."""

from unitelabs.zebra_ds8178.features import BeeperController
from unitelabs.zebra_ds8178.io import BeepCode, BeeperTone


async def test_beep_completes_on_the_simulated_scanner(simulated) -> None:
    """A beep pattern is acknowledged."""
    await BeeperController(await simulated()).beep(BeepCode.HIGH_LOW)


def test_pattern_offers_every_beep_code(registered) -> None:
    """The enum constraint lists each documented beep sequence, so clients cannot send an unknown code."""
    controller = registered(BeeperController)

    constraints = controller.commands["Beep"].parameters["pattern"].data_type.constraints
    allowed = next(c for c in constraints if type(c).__name__ == "Set")
    assert len(allowed.values) == len(BeepCode)
    assert list(controller.commands["Beep"].errors) == ["ScannerNotRespondingError"]


async def test_beep_after_scan_and_tone_can_be_turned_off_and_back(simulated) -> None:
    """Both settings are written and read back through the feature."""
    controller = BeeperController(await simulated())

    await controller.set_beep_after_scan(enabled=False)
    await controller.set_tone(BeeperTone.OFF)
    assert (await controller.get_beep_after_scan(), await controller.get_tone()) == (False, BeeperTone.OFF)

    await controller.set_beep_after_scan(enabled=True)
    await controller.set_tone(BeeperTone.MEDIUM)
    assert (await controller.get_beep_after_scan(), await controller.get_tone()) == (True, BeeperTone.MEDIUM)


def test_tone_offers_the_prg_values_and_every_endpoint_declares_not_responding(registered) -> None:
    """The enum lists the five documented tones; each endpoint talks to the scanner, so each can fail."""
    controller = registered(BeeperController)

    constraints = controller.commands["SetTone"].parameters["tone"].data_type.constraints
    allowed = next(c for c in constraints if type(c).__name__ == "Set")
    assert len(allowed.values) == len(BeeperTone)
    endpoints = {**controller.commands, **controller.properties}
    assert {name: list(endpoint.errors) for name, endpoint in endpoints.items()} == {
        name: ["ScannerNotRespondingError"] for name in ("Beep", "SetBeepAfterScan", "SetTone", "BeepAfterScan", "Tone")
    }
