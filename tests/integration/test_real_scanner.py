"""
Checks against a real DS8178 on its CR8178 cradle; skipped unless run with `--serial-port`.

    uv run pytest tests/integration --serial-port /dev/cu.usbmodem1201 -s
    uv run pytest tests/integration --serial-port /dev/cu.usbmodem1201 -s --with-barcode   # also reads a bar code

Nothing here changes the scanner for good: the tests read, beep, switch the aiming pattern on and off, disable
scanning only to enable it again, and change the beeper settings temporarily before restoring them. Put the scanner in "SSI over USB CDC" mode first (Product Reference Guide p. 8-6).
"""

import asyncio
import datetime

import pytest

from unitelabs.zebra_ds8178.io import CommandRejectedError, ScanStoppedError, Symbology, ZebraDs8178Protocol
from unitelabs.zebra_ds8178.io.ssi import BeepCode, BeeperTone


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


async def test_beeper_settings_are_read_back_and_restored(scanner: ZebraDs8178Protocol) -> None:
    """The temporary writes reach the scanner if the scanner reports them back; the original values are restored."""
    beep_after_scan, tone = await scanner.get_beep_after_good_decode(), await scanner.get_beeper_tone()
    print(f"before: beep after scan={beep_after_scan} tone={tone.name}")
    try:
        await scanner.set_beep_after_good_decode(enabled=not beep_after_scan)
        await scanner.set_beeper_tone(BeeperTone.OFF if tone is not BeeperTone.OFF else BeeperTone.MEDIUM)

        assert await scanner.get_beep_after_good_decode() is (not beep_after_scan)
        assert (await scanner.get_beeper_tone() is BeeperTone.OFF) is (tone is not BeeperTone.OFF)
    finally:
        await scanner.set_beep_after_good_decode(enabled=beep_after_scan)
        await scanner.set_beeper_tone(tone)


async def test_a_disabled_scanner_refuses_a_session(scanner: ZebraDs8178Protocol) -> None:
    """SCAN_DISABLE makes the scanner itself deny START_DECODE, as the simulated scanner does."""
    await scanner.scan_disable()
    try:
        with pytest.raises(CommandRejectedError, match="DENIED"):
            await scanner.start_decode()
    finally:
        await scanner.scan_enable()


async def test_stop_scan_ends_a_read(scanner: ZebraDs8178Protocol) -> None:
    """Keep bar codes away: the scanner's illumination should go off after about a second, not after 9.9 s."""
    scan = asyncio.create_task(scanner.scan(timeout=9.9))
    await asyncio.sleep(1)

    await scanner.stop_scan()

    with pytest.raises(ScanStoppedError):
        await scan


async def test_scan_reads_the_bar_code_in_front(scanner: ZebraDs8178Protocol, barcode: None) -> None:
    """Hold a bar code in front of the window, without pulling the trigger, when this test starts."""
    decoded = await scanner.scan(timeout=9.9)
    print(f"scan -> {decoded.symbology.name} {decoded.data!r}")

    assert decoded.symbology is not Symbology.UNKNOWN
    assert decoded.data
