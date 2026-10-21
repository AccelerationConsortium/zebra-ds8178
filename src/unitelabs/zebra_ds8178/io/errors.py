"""
Errors the connector raises when talking to the DS8178.

The docstrings become the SiLA Defined Execution Error descriptions, so each states what happened and then how an
operator can recover.
"""


class SerialPortNotFoundError(Exception):
    """
    No Zebra cradle was found on a serial port, or the configured serial port could not be opened.

    Check that the cradle's USB cable is plugged in and that the scanner is in "SSI over USB CDC" mode; in its
    default keyboard mode it does not appear as a serial port. On Windows, install the Zebra USB CDC driver first.
    If several cradles are connected, set the serial port in the configuration, then retry.
    """


class ScannerNotRespondingError(Exception):
    """
    The scanner did not answer, or the connector has no open connection to it.

    Place the scanner in its cradle or within Bluetooth range and pull the trigger to wake it if it stopped trying to
    reconnect; re-pair it by scanning the bar code on the cradle if needed. After a lost connection, use Start Real
    Mode to reconnect.
    """


class UnexpectedResponseError(Exception):
    """
    The scanner sent a packet that does not match the format in the SSI Programmer's Guide.

    The raw bytes are kept in the message. Check that the scanner is in "SSI over USB CDC" mode and that no other
    program has the serial port open, then retry.
    """


class CommandRejectedError(Exception):
    """
    The scanner understood the request but refused it.

    The message gives the scanner's reason. The value may be out of range or the command not supported by this
    scanner's firmware, so retrying the same request will not help.
    """


class ScannerBusyError(Exception):
    """
    A scan is already in progress, so the scanner cannot start another one.

    Wait for the running scan to finish or stop it, then retry.
    """


class ScanningDisabledError(Exception):
    """
    Scanning is disabled, so the scanner ignores both the trigger and scan requests.

    Enable scanning, then retry.
    """


class NoBarcodeDecodedError(Exception):
    """
    The scanner did not decode a bar code before the scan timed out.

    Check that the bar code is in the scanner's field of view, that its symbology is enabled and that the label is
    not damaged. Below 3% battery the scanner stops scanning (Product Reference Guide p. 1-8); leave it in the cradle
    to charge, then retry.
    """
