from unitelabs.cdk import sila

from ...constants import CATEGORY, ORIGINATOR
from ...io import BeepCode, BeeperTone, ScannerNotRespondingError, ZebraDs8178Protocol


class BeeperController(sila.Feature):
    """
    Controls the beeper of the Zebra DS8178 scanner.

    Beep sounds a pattern, e.g. to signal an operator where to scan next. In an automated workflow, Set Beep After
    Scan turns off the beep after each bar code read while errors are still heard, and Set Tone to off silences the
    beep after reads too; Beep and the beep when the scanner loses its cradle are still heard either way. Changing
    either setting does not itself beep. Both settings last until the scanner is switched off or its battery runs
    out; restarting the connector does not reset them.
    """

    def __init__(self, protocol: ZebraDs8178Protocol):
        super().__init__(originator=ORIGINATOR, category=CATEGORY)
        self.protocol = protocol

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def beep(self, pattern: BeepCode) -> None:
        """
        Sound a beep pattern; it is heard even while the tone is off.

        Args:
          Pattern: The beep sequence, named by length, pitch and count, e.g. 'short high 1' for one short high beep.
        """
        await self.protocol.beep(pattern)

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_beep_after_scan(self) -> bool:
        """Whether the scanner beeps after each bar code it reads, as reported by the scanner."""
        return await self.protocol.get_beep_after_good_decode()

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def set_beep_after_scan(self, enabled: bool) -> None:
        """
        Turn the beep after each bar code read on or off; the scanner still beeps to indicate errors.

        The setting lasts until the scanner is switched off or its battery runs out; it then returns to the setting
        stored on the scanner, on by default.

        Args:
          Enabled: Whether the scanner beeps after each bar code it reads.
        """
        await self.protocol.set_beep_after_good_decode(enabled)

    @sila.UnobservableProperty(errors=[ScannerNotRespondingError])
    async def get_tone(self) -> BeeperTone:
        """Pitch of the scanner's beeps, or off when its own beeps are silenced, as reported by the scanner."""
        return await self.protocol.get_beeper_tone()

    @sila.UnobservableCommand(errors=[ScannerNotRespondingError])
    async def set_tone(self, tone: BeeperTone) -> None:
        """
        Set the pitch of the scanner's beeps, or silence them.

        Off silences the beep after each read; Beep and the beep when the scanner loses its cradle are still heard.
        The setting lasts until the scanner is switched off or its battery runs out; it then returns to the tone stored
        on the scanner, medium by default.

        Args:
          Tone: The pitch, e.g. 'medium', or 'off' to silence the scanner's own beeps.
        """
        await self.protocol.set_beeper_tone(tone)
