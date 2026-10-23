"""The simulated scanner, driven through the real protocol so the interface behaves as on hardware."""

import collections.abc
import datetime

import pytest

from unitelabs.zebra_ds8178.io import (
    CommandRejectedError,
    NoBarcodeDecodedError,
    ScanningDisabledError,
    SimulatedScanner,
    ZebraDs8178Protocol,
)
from unitelabs.zebra_ds8178.io.ssi import BeepCode, Opcode, Packet, Symbology


@pytest.fixture
async def simulate() -> collections.abc.AsyncGenerator[collections.abc.Callable, None]:
    """Open a protocol to a simulated scanner."""
    opened: list[ZebraDs8178Protocol] = []

    async def _simulate(scanner: SimulatedScanner | None = None) -> ZebraDs8178Protocol:
        protocol = ZebraDs8178Protocol(simulated_scanner=scanner)
        await protocol.open_simulation()
        opened.append(protocol)
        return protocol

    yield _simulate
    for protocol in opened:
        await protocol.aclose()


async def test_connects_like_a_real_scanner(simulate) -> None:
    """The connect sequence completes and the revision has the cradle's format."""
    protocol = await simulate()

    assert await protocol.get_revision() == "PAACYS00-010-R01"
    assert protocol.scanning_enabled


async def test_scans_cycle_through_the_appendix_f_samples(simulate) -> None:
    """Each host scan decodes the next sample bar code, as scanned from the PRG on hardware."""
    protocol = await simulate()

    first, second = await protocol.scan(timeout=1), await protocol.scan(timeout=1)

    assert (first.symbology, first.text) == (Symbology.UPC_A, "012345678905")
    assert (second.symbology, second.text) == (Symbology.UPC_E, "04252614")


async def test_without_a_bar_code_scans_time_out(simulate) -> None:
    """`barcode_present=False` lets clients try the no-read path."""
    protocol = await simulate(SimulatedScanner(barcode_present=False))

    with pytest.raises(NoBarcodeDecodedError):
        await protocol.scan(timeout=0.5)


async def test_a_disabled_scanner_refuses_sessions_like_the_hardware(simulate) -> None:
    """The connector refuses first; a session started anyway is DENIED, as the DS8178 does."""
    protocol = await simulate()
    await protocol.scan_disable()

    with pytest.raises(ScanningDisabledError):
        await protocol.scan(timeout=1)
    with pytest.raises(CommandRejectedError, match="START_DECODE.*DENIED"):
        await protocol.start_decode()


async def test_parameters_are_kept_and_unsupported_ones_left_out(simulate) -> None:
    """Writes are read back; a parameter the simulated scanner does not know is absent, not an error."""
    protocol = await simulate()

    await protocol.set_parameters({136: 50})

    assert await protocol.get_parameters(136, 238, 999) == {136: 50, 238: 1}


async def test_all_parameters_can_be_requested(simulate) -> None:
    """The ALL_PARAMETERS code returns every parameter the simulated scanner holds."""
    protocol = await simulate()

    reply = await protocol.request(Packet(Opcode.PARAM_REQUEST, b"\xfe"), reply=Opcode.PARAM_SEND)

    assert reply == bytes.fromhex("FF EE 01 88 63 8A 00")


async def test_state_survives_a_mode_switch(simulate) -> None:
    """Reopening the simulation keeps its settings, like a scanner left in its cradle."""
    protocol = await simulate()
    await protocol.set_parameters({136: 50})

    await protocol.open_simulation()

    assert await protocol.get_parameters(136) == {136: 50}


async def test_unknown_beep_codes_are_denied(simulate) -> None:
    """Valid beep codes are ACKed; others are refused as the SSI guide describes (p. 2-12)."""
    protocol = await simulate()

    await protocol.beep(BeepCode.LOW_CLICK)
    with pytest.raises(CommandRejectedError, match="DENIED"):
        await protocol.request(Packet(Opcode.BEEP, b"\x7f"))


async def test_reports_simulated_cradle_information(simulate) -> None:
    """The serial number marks the cradle as simulated, so it is never mistaken for a real one."""
    protocol = await simulate()

    assert await protocol.get_cradle_model() == "CR8178-SC100F4WW"
    assert await protocol.get_cradle_serial_number() == "SIMULATED"
    assert await protocol.get_cradle_manufacture_date() == datetime.date(2025, 1, 1)


async def test_unknown_opcodes_get_a_bad_context_nak(simulate) -> None:
    """Opcodes the simulated scanner does not handle are refused, not ignored."""
    protocol = await simulate()

    with pytest.raises(CommandRejectedError, match="BAD_CONTEXT"):
        await protocol.request(Packet(Opcode.SLEEP))
