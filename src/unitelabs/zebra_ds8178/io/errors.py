class UnexpectedResponseError(Exception):
    """
    The scanner sent a packet that does not match the format in the SSI Programmer's Guide.

    The raw bytes are kept in the message. Check that the scanner is in "SSI over USB CDC" mode and that no other
    program has the serial port open, then retry.
    """
