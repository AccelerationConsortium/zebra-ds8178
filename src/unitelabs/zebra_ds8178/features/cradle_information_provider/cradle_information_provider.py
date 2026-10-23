import datetime

from unitelabs.cdk import sila

from ...constants import CATEGORY, ORIGINATOR
from ...io import ScannerNotRespondingError, ZebraDs8178Protocol


class CradleInformationProvider(sila.Feature):
    """
    Identifies the CR8178 cradle the connector is connected to.

    The connector talks to the scanner through its cradle, and the cradle answers these requests itself: the values
    belong to the cradle, not to the handheld scanner, whose own serial number and battery are not reachable this way.
    """

    def __init__(self, protocol: ZebraDs8178Protocol):
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self.protocol = protocol

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_model(self) -> str:
        """Model number of the cradle, as printed on its label, e.g. CR8178-SC100F4WW."""
        return await self.protocol.get_cradle_model()

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_serial_number(self) -> str:
        """Serial number of the cradle, as printed on its label."""  # noqa: D401
        return await self.protocol.get_cradle_serial_number()

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_manufacture_date(self) -> datetime.date:
        """Date the cradle was made."""
        return await self.protocol.get_cradle_manufacture_date()

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_firmware_version(self) -> str:
        """Release name of the cradle's firmware, e.g. PAACYS00-010-R01."""
        return await self.protocol.get_revision()
