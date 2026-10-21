ZEBRA_USB_VENDOR_ID = 0x05E0
"""USB vendor ID of the cradle, used to find its serial port when none is configured."""

RESPONSE_TIMEOUT = 2.0
"""
Seconds to wait for an ACK/NAK or reply; matches the scanner's default Host Serial Response Time-out (Product
Reference Guide p. 9-17). Replies to parameter requests may legitimately take longer (SSI Programmer's Guide p. 2-55).
"""

RETRIES = 2
"""Resends after the first attempt before giving up (Product Reference Guide p. 9-6, SSI Programmer's Guide p. 2-21)."""

OPEN_TIMEOUT = 5.0
"""Seconds to wait for the serial port to open and the scanner to answer its first request."""
