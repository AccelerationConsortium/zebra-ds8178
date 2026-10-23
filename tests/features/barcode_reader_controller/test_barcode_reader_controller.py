"""Reading bar codes through the SiLA feature, against the simulated scanner."""

import asyncio

import pytest

from unitelabs.zebra_ds8178.features import BarcodeReaderController
from unitelabs.zebra_ds8178.io import Barcode, NoBarcodeDecodedError, ScanningDisabledError, SimulatedScanner, Symbology


async def test_scan_returns_the_bar_code_read(simulated) -> None:
    """The simulated scanner reads the first Appendix F sample."""
    controller = BarcodeReaderController(await simulated())

    assert await controller.scan(timeout=1) == Barcode(Symbology.UPC_A, "012345678905")


async def test_scan_without_a_bar_code_reports_it(simulated) -> None:
    """The defined error a client can react to, e.g. by asking the operator to present the label again."""
    controller = BarcodeReaderController(await simulated(SimulatedScanner(barcode_present=False)))

    with pytest.raises(NoBarcodeDecodedError):
        await controller.scan(timeout=0.5)


async def test_scanning_can_be_disabled_and_enabled(simulated) -> None:
    """While disabled, Scan is refused with its own error; enabling restores it."""
    controller = BarcodeReaderController(await simulated())

    await controller.set_scanning_enabled(enabled=False)
    assert await controller.get_scanning_enabled() is False
    with pytest.raises(ScanningDisabledError):
        await controller.scan(timeout=1)

    await controller.set_scanning_enabled(enabled=True)
    assert await controller.get_scanning_enabled() is True
    assert (await controller.scan(timeout=1)).symbology is Symbology.UPC_A


async def test_scanned_barcode_streams_bar_codes_read_after_subscribing(simulated) -> None:
    """A subscriber receives each new bar code; nothing read before subscribing is replayed."""
    controller = BarcodeReaderController(await simulated())
    await controller.scan(timeout=1)  # read before subscribing
    stream = controller.subscribe_scanned_barcode()
    received = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    try:
        await controller.scan(timeout=1)

        assert await asyncio.wait_for(received, timeout=1) == Barcode(Symbology.UPC_E, "04252614")
    finally:
        await stream.aclose()


async def test_aiming_starts_and_stops(simulated) -> None:
    """Both commands complete on an acknowledging scanner."""
    controller = BarcodeReaderController(await simulated())

    await controller.start_aiming()
    await controller.stop_aiming()


def test_scan_timeout_is_limited_to_the_scanners_session_in_seconds(registered) -> None:
    """The constraint keeps the timeout within the scanner's own Decode Session Timeout range."""
    controller = registered(BarcodeReaderController)

    command = controller.commands["Scan"]
    constraints = command.parameters["timeout"].data_type.constraints
    limits = {type(c).__name__: c.value.value for c in constraints if hasattr(c, "value")}
    assert limits == {"MinimalInclusive": 0.5, "MaximalInclusive": 9.9}
    assert [u.label for u in constraints if type(u).__name__ == "Unit"] == ["s"]


def test_endpoints_declare_the_errors_a_client_can_react_to(registered) -> None:
    """Only the errors each endpoint can raise are declared; the local state and stream declare none."""
    controller = registered(BarcodeReaderController)

    assert sorted(controller.commands["Scan"].errors) == [
        "NoBarcodeDecodedError",
        "ScannerBusyError",
        "ScannerNotRespondingError",
        "ScanningDisabledError",
    ]
    for name in ("SetScanningEnabled", "StartAiming", "StopAiming"):
        assert list(controller.commands[name].errors) == ["ScannerNotRespondingError"]
    assert not controller.properties["ScanningEnabled"].errors
    assert not controller.properties["ScannedBarcode"].errors
