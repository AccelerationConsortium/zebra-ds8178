"""
Checks against a real DS8178 on its CR8178 cradle; skipped unless run with `--serial-port`.

    uv run pytest tests/integration --serial-port /dev/cu.usbmodem1201 -s
    uv run pytest tests/integration --serial-port /dev/cu.usbmodem1201 -s --with-barcode   # also reads a bar code

Nothing here changes the scanner for good: the tests read, beep, switch the aiming pattern on and off, and disable
scanning only to enable it again. Put the scanner in "SSI over USB CDC" mode first (Product Reference Guide p. 8-6).
"""

import datetime

import pytest

from unitelabs.zebra_ds8178.io import CommandRejectedError, Symbology, ZebraDs8178Protocol
from unitelabs.zebra_ds8178.io.ssi import BeepCode


async def test_connecting_leaves_packeted_decode_data_set(scanner: ZebraDs8178Protocol) -> None:
    """The connect sequence switched parameter 238 to packeted, or found it already set."""
    assert (await scanner.get_parameters(238)) == {238: 1}


async def test_reports_the_cradle_identity(scanner: ZebraDs8178Protocol) -> None:
    """The attribute reads reach the cradle, and its values are what its label shows."""
    model, serial = await scanner.get_cradle_model(), await scanner.get_cradle_serial_number()
    made, firmware = await scanner.get_cradle_manufacture_date(), await scanner.get_revision()
    print(f"model={model} serial={serial} made={made} firmware={firmware}")

    assert model.startswith("CR8178")
    assert serial
    assert made <= datetime.date.today()
    assert firmware


async def test_beep_and_aiming_are_acknowledged(scanner: ZebraDs8178Protocol) -> None:
    """You should hear one short beep and see the red aiming pattern flash briefly."""
    await scanner.beep(BeepCode.SHORT_HIGH_1)
    await scanner.aim_on()
    await scanner.aim_off()


async def test_a_disabled_scanner_refuses_a_session(scanner: ZebraDs8178Protocol) -> None:
    """SCAN_DISABLE makes the scanner itself deny START_DECODE, as the simulated scanner does."""
    await scanner.scan_disable()
    try:
        with pytest.raises(CommandRejectedError, match="DENIED"):
            await scanner.start_decode()
    finally:
        await scanner.scan_enable()


async def test_scan_reads_the_bar_code_in_front(scanner: ZebraDs8178Protocol, barcode: None) -> None:
    """Hold a bar code in front of the window, without pulling the trigger, when this test starts."""
    decoded = await scanner.scan(timeout=9.9)
    print(f"scan -> {decoded.symbology.name} {decoded.data!r}")

    assert decoded.symbology is not Symbology.UNKNOWN
    assert decoded.data
