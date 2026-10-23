"""Sounding the beeper through the SiLA feature."""

from unitelabs.zebra_ds8178.features import BeeperController
from unitelabs.zebra_ds8178.io import BeepCode


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
