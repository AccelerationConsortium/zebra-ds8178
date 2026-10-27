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

    Scan starts a read as if the trigger were pulled and returns the first bar code decoded. Every bar code read, by
    Scan or by pulling the trigger, is also sent to subscribers of Scanned Barcode. The scanner talks to the connector
    through its cradle over Bluetooth, so it must be paired to the cradle and within range.
    """

    def __init__(self, protocol: ZebraDs8178Protocol):
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self.protocol = protocol

    @sila.ObservableCommand(errors=SCAN_ERRORS)
    async def scan(self, timeout: ScanTimeout) -> Barcode:
        """
        Read one bar code, as if the trigger were pulled, and return it.

        The scanner reads until it decodes a bar code or the timeout passes. A bar code read by pulling the trigger
        during that time is returned as well. Stop Scan ends the read early.

        Args:
          Timeout: How long to try to read a bar code.

        Returns:
          Barcode: The bar code read.
        """
        return await self.protocol.scan(timeout)

    @sila.UnobservableCommand()
    async def stop_scan(self) -> None:
        """
        Stop a running Scan, which then ends with ScanStoppedError; does nothing when no scan is running.

        Returns once the scanner has stopped reading.
        """
        await self.protocol.stop_scan()

    @sila.ObservableProperty()
    async def subscribe_scanned_barcode(self) -> sila.Stream[Barcode]:
        """Each bar code the scanner reads from now on, by Scan or by pulling the trigger; earlier ones are not sent."""
        async for decoded in self.protocol.decodes():
            yield decoded

    @sila.UnobservableProperty()
    async def get_scanning_enabled(self) -> bool:
        """Whether the scanner reads bar codes; while disabled, it ignores both the trigger and Scan."""
        return self.protocol.scanning_enabled

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def set_scanning_enabled(self, enabled: bool) -> None:
        """
        Enable or disable reading bar codes, by the trigger and by Scan.

        Scanning is enabled again whenever the connector connects to the scanner.

        Args:
          Enabled: Whether the scanner reads bar codes.
        """
        if enabled:
            await self.protocol.scan_enable()
        else:
            await self.protocol.scan_disable()

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def start_aiming(self) -> None:
        """Project the scanner's red aiming pattern, e.g. to line a label up with it, until Stop Aiming is called."""
        await self.protocol.aim_on()

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def stop_aiming(self) -> None:
        """Switch the aiming pattern off."""
        await self.protocol.aim_off()
