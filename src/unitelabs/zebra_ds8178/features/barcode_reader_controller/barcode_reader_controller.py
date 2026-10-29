import typing_extensions as typing

from unitelabs.cdk import sila

from ...constants import CATEGORY, MAX_SCAN_TIMEOUT, MIN_SCAN_TIMEOUT, ORIGINATOR
from ...io import SCAN_ERRORS, Barcode, ScannerNotRespondingError, ZebraDs8178Protocol
from ...io.units import SECOND

ScanTimeout = typing.Annotated[
    float,
    SECOND,
    sila.constraints.MinimalInclusive(MIN_SCAN_TIMEOUT),
    sila.constraints.MaximalInclusive(MAX_SCAN_TIMEOUT),  # the scanner ends a session after at most 9.9 s
]


class BarcodeReaderController(sila.Feature):
    """
    Reads bar codes with the Zebra DS8178 scanner.

    `BarcodeReaderController.Scan` starts a read as if the trigger were pulled and returns the first bar code
    decoded. Every bar code read, by `BarcodeReaderController.Scan` or by pulling the trigger, is also sent to
    subscribers of `BarcodeReaderController.ScannedBarcode`. The scanner talks to the connector through its cradle
    over Bluetooth, so it must be paired to the cradle and within range.
    """

    def __init__(self, protocol: ZebraDs8178Protocol):
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self.protocol = protocol

    @sila.ObservableCommand(errors=SCAN_ERRORS)
    async def scan(self, timeout: ScanTimeout) -> Barcode:
        """
        Read one bar code, as if the trigger were pulled, and return it.

        The scanner reads until it decodes a bar code or the timeout passes. A bar code read by pulling the trigger
        during that time is returned as well. `BarcodeReaderController.StopScan` ends the read early.

        Args:
          Timeout: How long to try to read a bar code.

        Returns:
          Barcode: The bar code read.
        """
        return await self.protocol.scan(timeout)

    @sila.UnobservableCommand()
    async def stop_scan(self) -> None:
        """
        Stop a running `BarcodeReaderController.Scan`, which then ends with ScanStoppedError; does nothing otherwise.

        Returns once the scanner has stopped reading.
        """
        await self.protocol.stop_scan()

    @sila.ObservableProperty(errors=[ScannerNotRespondingError])
    async def subscribe_scanned_barcode(self) -> sila.Stream[Barcode]:
        """
        Each bar code the scanner reads from now on, by `BarcodeReaderController.Scan` or by pulling the trigger.

        Bar codes read before subscribing are not sent.

        The subscription ends when the connection to the scanner is lost or the mode is switched; subscribe again.
        """
        async for decoded in self.protocol.decodes():
            yield decoded

    @sila.UnobservableProperty()
    async def get_scanning_enabled(self) -> bool:
        """Whether the scanner reads bar codes; while disabled, it ignores the trigger and Scan requests."""
        return self.protocol.scanning_enabled

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def set_scanning_enabled(self, enabled: bool) -> None:
        """
        Enable or disable reading bar codes, by the trigger and by `BarcodeReaderController.Scan`.

        Scanning is enabled again whenever the connector connects to the scanner. A scanner that restarts while
        disabled, e.g. after its battery ran out, reads one more bar code before the connector disables it again.

        Args:
          Enabled: Whether the scanner reads bar codes.
        """
        await self.protocol.set_scanning_enabled(enabled)

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def start_aiming(self) -> None:
        """Project the scanner's red aiming pattern, until `BarcodeReaderController.StopAiming`."""
        await self.protocol.aim_on()

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def stop_aiming(self) -> None:
        """Switch the aiming pattern off."""
        await self.protocol.aim_off()
