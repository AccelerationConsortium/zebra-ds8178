from unitelabs.cdk import sila

from ...constants import CATEGORY, ORIGINATOR
from ...io import BeepCode, ScannerNotRespondingError, ZebraDs8178Protocol


class BeeperController(sila.Feature):
    """Sounds the beeper of the Zebra DS8178 scanner, e.g. to signal an operator where to scan next."""

    def __init__(self, protocol: ZebraDs8178Protocol):
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self.protocol = protocol

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def beep(self, pattern: BeepCode) -> None:
        """
        Sound a beep pattern.

        Args:
          Pattern: The beep sequence, named by length, pitch and count, e.g. SHORT_HIGH_1 for one short high beep.
        """
        await self.protocol.beep(pattern)
