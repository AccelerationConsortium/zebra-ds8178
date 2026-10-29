"""Reading bar codes through the SiLA feature, against the simulated scanner."""

import asyncio

import pytest

from unitelabs.zebra_ds8178.features import BarcodeReaderController
from unitelabs.zebra_ds8178.io import (
    Barcode,
    NoBarcodeDecodedError,
    ScannerNotRespondingError,
    ScanningDisabledError,
    ScanStoppedError,
    SimulatedScanner,
    Symbology,
)


async def test_scan_returns_the_bar_code_read(simulated) -> None:
    """The simulated scanner reads the first Appendix F sample."""
    controller = BarcodeReaderController(await simulated())

    assert await controller.scan(timeout=1) == Barcode(Symbology.UPC_A, "012345678905")


async def test_scan_without_a_bar_code_reports_it(simulated) -> None:
    """The defined error a client can react to, e.g. by asking the operator to present the label again."""
    controller = BarcodeReaderController(await simulated(SimulatedScanner(barcode_present=False)))

    with pytest.raises(NoBarcodeDecodedError):
        await controller.scan(timeout=0.5)


async def test_stop_scan_ends_a_running_scan(simulated) -> None:
    """A client waiting on a long Scan can end it; the scan reports that it was stopped."""
    controller = BarcodeReaderController(await simulated(SimulatedScanner(barcode_present=False)))
    scan = asyncio.create_task(controller.scan(timeout=9.9))
    await asyncio.sleep(0.05)

    await controller.stop_scan()

    with pytest.raises(ScanStoppedError):
        await asyncio.wait_for(scan, timeout=1)


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


async def test_scanned_barcode_ends_with_not_responding_when_the_scanner_is_gone(simulated) -> None:
    """A subscriber learns that the connection ended instead of waiting forever."""
    protocol = await simulated()
    stream = BarcodeReaderController(protocol).subscribe_scanned_barcode()
    received = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    try:
        protocol.close()

        with pytest.raises(ScannerNotRespondingError):
            await asyncio.wait_for(received, timeout=1)
    finally:
        await stream.aclose()


async def test_unsubscribing_cancels_the_stream(simulated) -> None:
    """What the SiLA server does when a client unsubscribes: cancel the pending read and await it."""
    stream = BarcodeReaderController(await simulated()).subscribe_scanned_barcode()
    try:
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):  # a StopAsyncIteration here makes gRPC log ExecuteBatchError
            await pending
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
    """Only the errors each endpoint can raise are declared; the locally held state declares none."""
    controller = registered(BarcodeReaderController)

    assert sorted(controller.commands["Scan"].errors) == [
        "NoBarcodeDecodedError",
        "ScanStoppedError",
        "ScannerBusyError",
        "ScannerNotRespondingError",
        "ScanningDisabledError",
    ]
    assert not controller.commands["StopScan"].errors
    for name in ("SetScanningEnabled", "StartAiming", "StopAiming"):
        assert list(controller.commands[name].errors) == ["ScannerNotRespondingError"]
    assert not controller.properties["ScanningEnabled"].errors
    assert list(controller.properties["ScannedBarcode"].errors) == ["ScannerNotRespondingError"]
