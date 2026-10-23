ORIGINATOR = "ca.accelerationconsortium"
"""Reverse-domain originator of every feature; the Acceleration Consortium's, as in its other connectors."""

CATEGORY = "barcodereader"
"""Functional category of every DS8178 feature."""

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

CHARACTER_TIMEOUT = 0.5
"""
Seconds without a new byte after which a partly received packet is dropped; the scanner's own Host Character
Time-out defaults to 0.2 s (Product Reference Guide p. 9-18), the rest is margin for USB scheduling.
"""

MIN_SCAN_TIMEOUT = 0.5
"""Shortest scan, in seconds; the scanner's Decode Session Timeout starts at 0.5 s (Product Reference Guide p. 6-31)."""

MAX_SCAN_TIMEOUT = 9.9
"""
Longest scan, in seconds: the scanner ends a decode session on its own after its Decode Session Timeout, at most
9.9 s and 9.9 s by default (Product Reference Guide p. 6-31), so a longer wait would only outlast the session.
"""

FLUSH_TIMEOUT = 0.1
"""Seconds to let queued writes, e.g. the ACK for the last scan, reach the scanner before the port closes."""
