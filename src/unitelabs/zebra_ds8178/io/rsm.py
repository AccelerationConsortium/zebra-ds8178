"""
Zebra Remote Scanner Management (RSM) attribute reads, carried in SSI_MGMT_COMMAND packets.

Sources: DS8178 Product Reference Guide ("PRG") p. 9-8 to 9-9, the only documented example, and the Zebra Scanner
SDK Attribute Data Dictionary for attribute numbers and types. Neither defines the RSM layout; it is inferred from
the PRG example and confirmed on hardware (DS8178 on a CR8178 cradle, SSI over USB CDC):

- Request: `<total length:2> <command> 00 <attribute:2>... 42 00`. The trailing `42 00` is copied from the PRG
  example; its meaning is undocumented.
- Reply to ATTRIBUTE_GET: `<total length:2> 02 00`, then per supported attribute
  `<attribute:2> <type> <property>` followed by the value, then `FF FF`. Unsupported attributes are left out.
  String ('S') values are `<size:2> <offset:2> <bytes>`; array ('A') values have an element type byte first.

Over USB CDC the attributes answered are the cradle's (device class "Cradle"); the scanner's own attributes, its
battery among them, are not reachable this way.
"""

import enum

from .errors import UnexpectedResponseError

GET_PACKET_SIZE = 0x20
"""Command the host must send before any other RSM command (PRG p. 9-9)."""

ATTRIBUTE_GET = 0x02

_REQUEST_TRAILER = b"\x42\x00"
_END_OF_ATTRIBUTES = b"\xff\xff"
_HEADER_LENGTH = 4


class Attribute(enum.IntEnum):
    """Attribute numbers read by the connector (Attribute Data Dictionary section 4.1)."""

    MODEL_NUMBER = 533
    SERIAL_NUMBER = 534
    DATE_OF_MANUFACTURE = 535


def encode_get_packet_size() -> bytes:
    """Encode the RSM Get Packet Size command exactly as in the PRG example: `00 06 20 00 FF FF` (p. 9-9)."""
    return _with_length(bytes([GET_PACKET_SIZE, 0x00]) + _END_OF_ATTRIBUTES)


def encode_attribute_get(*attributes: int) -> bytes:
    """Encode an ATTRIBUTE_GET for one or more attributes, e.g. `00 08 02 00 02 15 42 00` for the model number."""
    numbers = b"".join(attribute.to_bytes(2, "big") for attribute in attributes)
    return _with_length(bytes([ATTRIBUTE_GET, 0x00]) + numbers + _REQUEST_TRAILER)


def parse_attribute_get(data: bytes) -> dict[int, int | str | bytes]:
    """
    Parse an ATTRIBUTE_GET reply into values by attribute number.

    Strings are returned without their NUL terminator and space padding, arrays as bytes, and byte, flag and word
    values as int. The word ('W') and flag ('F') layouts follow the Attribute Data Dictionary sizes and are not
    verified on hardware.

    Raises:
      UnexpectedResponseError: If the reply's length or command does not match, an attribute is truncated, or it
        has a type the connector cannot read.
    """
    if len(data) < _HEADER_LENGTH + len(_END_OF_ATTRIBUTES) or int.from_bytes(data[:2], "big") != len(data):
        msg = f"Received an RSM reply of the wrong length {data.hex(' ')}."
        raise UnexpectedResponseError(msg)
    if data[2] != ATTRIBUTE_GET or not data.endswith(_END_OF_ATTRIBUTES):
        msg = f"Expected an RSM attribute reply, received {data.hex(' ')}."
        raise UnexpectedResponseError(msg)

    values: dict[int, int | str | bytes] = {}
    body, position = data[_HEADER_LENGTH : -len(_END_OF_ATTRIBUTES)], 0
    try:
        while position < len(body):
            attribute, kind = int.from_bytes(_read(body, position, 2), "big"), chr(_read(body, position + 2, 1)[0])
            position += 4  # number, type and property
            if kind in "BF":
                values[attribute], position = _read(body, position, 1)[0], position + 1
            elif kind == "W":
                values[attribute], position = int.from_bytes(_read(body, position, 2), "big"), position + 2
            elif kind in "SA":
                position += kind == "A"  # the array's element type
                size = int.from_bytes(_read(body, position, 2), "big")
                value = _read(body, position + 4, size)  # after the size and the offset
                values[attribute] = value.split(b"\0", 1)[0].decode("ascii").rstrip() if kind == "S" else value
                position += 4 + size
            else:
                msg = f"Attribute {attribute} has type {kind!r}, which the connector cannot read."
                raise UnexpectedResponseError(msg)
    except (IndexError, UnicodeDecodeError) as error:
        msg = f"Received a truncated or malformed RSM attribute reply {data.hex(' ')}."
        raise UnexpectedResponseError(msg) from error

    return values


def _with_length(body: bytes) -> bytes:
    return (len(body) + 2).to_bytes(2, "big") + body


def _read(data: bytes, position: int, length: int) -> bytes:
    chunk = data[position : position + length]
    if len(chunk) != length:
        raise IndexError(position)

    return chunk
