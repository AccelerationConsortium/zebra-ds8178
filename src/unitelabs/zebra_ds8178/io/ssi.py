"""
Packets of Zebra's Simple Serial Interface (SSI).

Sources: SSI Programmer's Guide 72E-40451-04 ("SSI guide") and the DS8178 Product Reference Guide ("PRG"), chapter 9.
This module only builds and parses bytes; sending, ACK/NAK handshaking and retries live in the protocol.

Verified on hardware (DS8178 on a CR8178 cradle, SSI over USB CDC):
- Framing and checksum, and a NAK RESEND answered by a resend with the retransmit bit set; the scanner NAKs the
  first request after the port opens.
- REQUEST_REVISION, BEEP, and AIM_ON/AIM_OFF, which switch the red aiming pattern.
- PARAM_REQUEST with byte values and F0/F1 parameter numbers, and PARAM_SEND.
- START_DECODE, STOP_DECODE, and DECODE_DATA for the six Appendix F sample symbologies.
- SCAN_ENABLE/SCAN_DISABLE; a disabled scanner NAKs START_DECODE as DENIED.

Known gaps, to verify on hardware:
- The DS8178 sets bit 7 of the status byte on its replies, which the SSI guide marks as unused; it is masked off.
- LED_ON and LED_OFF are ACKed for LED bits 0 to 2 (bit 3 is DENIED) but light no LED on the DS8178 in its
  cradle, so they are not used. The PRG's Beeper/LED attribute 6000 would reach the cradle rather than the scanner
  over USB CDC (see `rsm`), and its set command is undocumented.
- The multipacket decode data format, string/array/multipacket parameter replies and the symbology IDs beyond the
  six sampled ones follow the SSI guide only.
"""

import dataclasses
import enum

from unitelabs.bus import Command

from ..constants import RESPONSE_TIMEOUT
from .errors import UnexpectedResponseError

HEADER_LENGTH = 4
"""Length, opcode, message source and status bytes (SSI guide p. 1-9)."""

CHECKSUM_LENGTH = 2

MAX_DATA_LENGTH = 251
"""Data bytes per packet; longer messages are multipacketed (SSI guide p. 1-9)."""


class Opcode(enum.IntEnum):
    """SSI opcodes the DS8178 supports, as listed in PRG table 9-1 (names as in the PRG)."""

    SSI_MGMT_COMMAND = 0x80
    REQUEST_REVISION = 0xA3
    REPLY_REVISION = 0xA4
    AIM_OFF = 0xC4
    AIM_ON = 0xC5
    PARAM_SEND = 0xC6
    PARAM_REQUEST = 0xC7
    PARAM_DEFAULTS = 0xC8
    CMD_ACK = 0xD0
    CMD_NAK = 0xD1
    CAPABILITIES_REQUEST = 0xD3
    CAPABILITIES_REPLY = 0xD4
    START_DECODE = 0xE4
    """Called START_SESSION in the SSI guide."""
    STOP_DECODE = 0xE5
    """Called STOP_SESSION in the SSI guide."""
    BEEP = 0xE6
    LED_ON = 0xE7
    LED_OFF = 0xE8
    SCAN_ENABLE = 0xE9
    SCAN_DISABLE = 0xEA
    SLEEP = 0xEB
    DECODE_DATA = 0xF3
    EVENT = 0xF6


class Source(enum.IntEnum):
    """Who sent a packet (SSI guide table 1-3)."""

    DECODER = 0x00
    HOST = 0x04


class Status(enum.IntFlag):
    """Bits of the status byte (SSI guide table 1-3); unused bits are 0."""

    NONE = 0
    RETRANSMIT = 1 << 0
    CONTINUATION = 1 << 1
    """Set on every packet of a multipacket message except the last."""
    PERMANENT = 1 << 3
    """For PARAM_SEND: store in non-volatile memory instead of until power-down."""


class NakCause(enum.IntEnum):
    """Reason in a CMD_NAK (SSI guide p. 2-25); the numbers it lists as reserved are left out."""

    RESEND = 1
    """Checksum failure; resend with the retransmit bit set."""
    BAD_CONTEXT = 2
    """Unexpected or unknown message."""
    DENIED = 6
    """The request was understood but refused, e.g. an out-of-range value."""
    CANCEL = 10
    """The receiver does not want the message in progress."""
    UNKNOWN = -1

    @classmethod
    def _missing_(cls, value: object) -> "NakCause":  # noqa: ARG003
        return cls.UNKNOWN


class EventCode(enum.IntEnum):
    """Codes in an EVENT packet the DS8178 reports (PRG table 9-4)."""

    DECODE = 0x01
    BOOT_UP = 0x03
    PARAMETER_ENTRY_ERROR = 0x07
    PARAMETER_STORED = 0x08
    DEFAULTS_SET = 0x0A
    NUMBER_EXPECTED = 0x0F
    UNKNOWN = -1

    @classmethod
    def _missing_(cls, value: object) -> "EventCode":  # noqa: ARG003
        return cls.UNKNOWN


class BeepCode(enum.IntEnum):
    """Beep sequences for BEEP and PARAM_SEND (SSI guide table 2-12); names read duration, pitch, count."""

    SHORT_HIGH_1 = 0x00
    SHORT_HIGH_2 = 0x01
    SHORT_HIGH_3 = 0x02
    SHORT_HIGH_4 = 0x03
    SHORT_HIGH_5 = 0x04
    SHORT_LOW_1 = 0x05
    SHORT_LOW_2 = 0x06
    SHORT_LOW_3 = 0x07
    SHORT_LOW_4 = 0x08
    SHORT_LOW_5 = 0x09
    LONG_HIGH_1 = 0x0A
    LONG_HIGH_2 = 0x0B
    LONG_HIGH_3 = 0x0C
    LONG_HIGH_4 = 0x0D
    LONG_HIGH_5 = 0x0E
    LONG_LOW_1 = 0x0F
    LONG_LOW_2 = 0x10
    LONG_LOW_3 = 0x11
    LONG_LOW_4 = 0x12
    LONG_LOW_5 = 0x13
    FAST_WARBLE = 0x14
    SLOW_WARBLE = 0x15
    HIGH_LOW = 0x16
    LOW_HIGH = 0x17
    HIGH_LOW_HIGH = 0x18
    LOW_HIGH_LOW = 0x19
    HIGH_HIGH_LOW_LOW = 0x1A
    SHORT_HIGH_13 = 0x1B
    HIGH_CLICK = 0x1C
    LOW_CLICK = 0x1D


NO_BEEP = 0xFF
"""Beep code for PARAM_SEND when no confirmation beep is wanted (SSI guide p. 2-58)."""


class Symbology(enum.Enum):
    """
    Bar code type in a DECODE_DATA packet, by SSI ID (SSI guide tables 2-37 and 2-38).

    The two tables disagree on two entries: Matrix 2 of 5 is 0x39 in table 2-37 and 0x71 in table 2-38 (both are
    mapped here), and 0x73 is "Korean 2 of 5" in table 2-37 but "Korean 3 of 5" in table 2-38; the latter is the
    symbology's actual name. Not verified on hardware.
    """

    NOT_APPLICABLE = 0x00
    CODE_39 = 0x01
    CODABAR = 0x02
    CODE_128 = 0x03
    DISCRETE_2_OF_5 = 0x04
    IATA = 0x05
    INTERLEAVED_2_OF_5 = 0x06
    CODE_93 = 0x07
    UPC_A = 0x08
    UPC_E = 0x09
    EAN_8 = 0x0A
    EAN_13 = 0x0B
    CODE_11 = 0x0C
    CODE_49 = 0x0D
    MSI = 0x0E
    GS1_128 = 0x0F
    UPC_E1 = 0x10
    PDF417 = 0x11
    CODE_16K = 0x12
    CODE_39_FULL_ASCII = 0x13
    TRIOPTIC_CODE_39 = 0x15
    BOOKLAND_EAN = 0x16
    COUPON_CODE = 0x17
    NW7 = 0x18
    ISBT_128 = 0x19
    MICRO_PDF417 = 0x1A
    DATA_MATRIX = 0x1B
    QR_CODE = 0x1C
    MICRO_PDF417_CCA = 0x1D
    POSTNET_US = 0x1E
    PLANET_US = 0x1F
    CODE_32 = 0x20
    ISBT_128_CONCATENATED = 0x21
    POSTAL_JAPAN = 0x22
    POSTAL_AUSTRALIA = 0x23
    POSTAL_DUTCH = 0x24
    MAXICODE = 0x25
    POSTBAR_CANADA = 0x26
    POSTAL_UK = 0x27
    MACRO_PDF417 = 0x28
    MACRO_QR_CODE = 0x29
    MICRO_QR_CODE = 0x2C
    AZTEC = 0x2D
    AZTEC_RUNE = 0x2E
    FRENCH_LOTTERY = 0x2F
    GS1_DATABAR_14 = 0x30
    GS1_DATABAR_LIMITED = 0x31
    GS1_DATABAR_EXPANDED = 0x32
    FOUR_STATE_US = 0x34
    FOUR_STATE_US4 = 0x35
    ISSN = 0x36
    SCANLET_WEBCODE = 0x37
    CUE_CAT = 0x38
    MATRIX_2_OF_5 = 0x39
    UPC_A_PLUS_2 = 0x48
    UPC_E_PLUS_2 = 0x49
    EAN_8_PLUS_2 = 0x4A
    EAN_13_PLUS_2 = 0x4B
    UPC_E1_PLUS_2 = 0x50
    COMPOSITE_CC_A_GS1_128 = 0x51
    COMPOSITE_CC_A_EAN_13 = 0x52
    COMPOSITE_CC_A_EAN_8 = 0x53
    COMPOSITE_CC_A_GS1_DATABAR_EXPANDED = 0x54
    COMPOSITE_CC_A_GS1_DATABAR_LIMITED = 0x55
    COMPOSITE_CC_A_GS1_DATABAR_14 = 0x56
    COMPOSITE_CC_A_UPC_A = 0x57
    COMPOSITE_CC_A_UPC_E = 0x58
    COMPOSITE_CC_C_GS1_128 = 0x59
    TLC_39 = 0x5A
    COMPOSITE_CC_B_GS1_128 = 0x61
    COMPOSITE_CC_B_EAN_13 = 0x62
    COMPOSITE_CC_B_EAN_8 = 0x63
    COMPOSITE_CC_B_GS1_DATABAR_EXPANDED = 0x64
    COMPOSITE_CC_B_GS1_DATABAR_LIMITED = 0x65
    COMPOSITE_CC_B_GS1_DATABAR_14 = 0x66
    COMPOSITE_CC_B_UPC_A = 0x67
    COMPOSITE_CC_B_UPC_E = 0x68
    SIGNATURE = 0x69
    CHINESE_2_OF_5 = 0x72
    KOREAN_3_OF_5 = 0x73
    UPC_A_PLUS_5 = 0x88
    UPC_E_PLUS_5 = 0x89
    EAN_8_PLUS_5 = 0x8A
    EAN_13_PLUS_5 = 0x8B
    UPC_E1_PLUS_5 = 0x90
    MACRO_MICRO_PDF417 = 0x9A
    OCR_B = 0xA0
    GS1_DATABAR_EXPANDED_COUPON = 0xB4
    HAN_XIN = 0xB7
    GS1_DATA_MATRIX = 0xC1
    GS1_QR = 0xC2
    MAILMARK = 0xC3
    DOTCODE = 0xC4
    MULTICODE = 0xC6
    UK_PLESSEY = 0xC7
    GRID_MATRIX = 0xC8
    TELEPEN = 0xCA
    UDI_PARSED_CODE = 0xCC
    RFID_RAW = 0xE0
    RFID_URI = 0xE1
    UNKNOWN = -1

    @classmethod
    def _missing_(cls, value: object) -> "Symbology":
        return cls.MATRIX_2_OF_5 if value == 0x71 else cls.UNKNOWN


MULTIPACKET_FORMAT = 0x99
"""Bar code type marking structured decode data; the actual type is embedded in the data (SSI guide p. 2-28)."""


def checksum(data: bytes) -> int:
    """Compute the 2's complement of the 16-bit sum of all bytes before the checksum (SSI guide table 1-3)."""
    return -sum(data) & 0xFFFF


@dataclasses.dataclass(frozen=True)
class Packet:
    """
    One SSI packet: `<length> <opcode> <source> <status> <data...> <checksum high> <checksum low>`.

    The length counts the header and data but not the checksum (SSI guide p. 1-9).
    """

    opcode: int
    data: bytes = b""
    source: Source = Source.HOST
    status: Status = Status.NONE

    @property
    def is_continued(self) -> bool:
        """Whether more packets of the same multipacket message follow."""
        return Status.CONTINUATION in self.status

    def encode(self) -> bytes:
        """Encode the packet as sent on the wire."""
        body = bytes([HEADER_LENGTH + len(self.data), self.opcode, self.source, self.status]) + self.data
        return body + checksum(body).to_bytes(CHECKSUM_LENGTH, "big")

    @classmethod
    def decode(cls, frame: bytes) -> "Packet":
        """
        Parse one complete frame, e.g. one cut from the stream with `frame_length`.

        Raises:
          UnexpectedResponseError: If the frame is shorter than its length byte announces or the checksum is wrong.
        """
        if len(frame) < HEADER_LENGTH + CHECKSUM_LENGTH or len(frame) != frame_length(frame[0]):
            msg = f"Received a malformed SSI packet {frame.hex(' ')}."
            raise UnexpectedResponseError(msg)

        body, received = frame[:-CHECKSUM_LENGTH], int.from_bytes(frame[-CHECKSUM_LENGTH:], "big")
        if checksum(body) != received:
            msg = f"Received an SSI packet with a wrong checksum {frame.hex(' ')}."
            raise UnexpectedResponseError(msg)

        try:
            source = Source(body[2])
        except ValueError as error:
            msg = f"Received an SSI packet from an unknown source {frame.hex(' ')}."
            raise UnexpectedResponseError(msg) from error

        return cls(opcode=body[1], source=source, status=Status(body[3] & 0x0F), data=body[HEADER_LENGTH:])


def frame_length(length_byte: int) -> int:
    """Total bytes on the wire for a packet whose first byte is `length_byte`."""
    return length_byte + CHECKSUM_LENGTH


def join_message(packets: list[Packet]) -> Packet:
    """
    Reassemble the packets of a multipacket message into one packet carrying the whole message.

    DECODE_DATA repeats its bar code type and PARAM_SEND its beep code in every packet (SSI guide notes to tables 2-37
    and 2-72), so that first byte is kept only from the first packet.
    """
    first, rest = packets[0], packets[1:]
    skip = 1 if first.opcode in (Opcode.DECODE_DATA, Opcode.PARAM_SEND) else 0
    data = first.data + b"".join(packet.data[skip:] for packet in rest)

    return Packet(opcode=first.opcode, data=data, source=first.source, status=packets[-1].status)


class SsiCommand(Command[Packet, Packet]):
    """A request packet and the reply message it gets, which may arrive as several packets and is joined into one."""

    def __init__(self, packet: Packet) -> None:
        super().__init__(packet, timeout=RESPONSE_TIMEOUT)

    def _serialize(self, message: Packet) -> bytes:
        return message.encode()

    def _validate_response(self, data: bytes) -> bool:
        # The protocol forwards whole, checksum-verified frames; the reply is complete once the last frame is.
        position = status = 0
        while position < len(data):
            status = data[position + 3]
            position += frame_length(data[position])

        return position == len(data) and not status & Status.CONTINUATION

    def _deserialize(self, response: bytes) -> Packet:
        packets, position = [], 0
        while position < len(response):
            end = position + frame_length(response[position])
            packets.append(Packet.decode(response[position:end]))
            position = end

        return join_message(packets)


@dataclasses.dataclass(frozen=True)
class Barcode:
    """
    A bar code decoded by the scanner.

    Attributes:
      Symbology: The bar code type.
      Data: The decoded content, including any prefix and suffix configured on the scanner.
    """

    symbology: Symbology
    data: str


def parse_decode_data(data: bytes) -> Barcode:
    """
    Parse the data of a reassembled DECODE_DATA message: `<bar code type> <decode data...>` (SSI guide table 2-34).

    For the multipacket format (type 0x99) the data is `<actual type> <count>` followed by `count` segments of
    `<spare> <length high> <length low> <bytes...>` (SSI guide table 2-36); the segments are joined in order, which
    for structured append places the control block after the main data. Not verified on hardware.

    The SSI guide calls the data "ASCII" (p. 2-28), but 2D codes may carry UTF-8 and GS1 codes carry the GS (0x1D)
    separator; it is decoded as UTF-8, which covers both, with invalid bytes replaced rather than dropped.

    Raises:
      UnexpectedResponseError: If the data is empty or a multipacket segment is truncated.
    """
    if not data:
        msg = "Received a DECODE_DATA packet without a bar code type."
        raise UnexpectedResponseError(msg)

    if data[0] != MULTIPACKET_FORMAT:
        return Barcode(symbology=Symbology(data[0]), data=_decode_text(data[1:]))

    if len(data) < 3:
        msg = f"Received a truncated multipacket DECODE_DATA {data.hex(' ')}."
        raise UnexpectedResponseError(msg)

    segments, position = [], 3
    for _ in range(data[2]):
        length = int.from_bytes(data[position + 1 : position + 3], "big")
        segment = data[position + 3 : position + 3 + length]
        if len(segment) != length:
            msg = f"Received a truncated multipacket DECODE_DATA {data.hex(' ')}."
            raise UnexpectedResponseError(msg)
        segments.append(segment)
        position += 3 + length

    return Barcode(symbology=Symbology(data[1]), data=_decode_text(b"".join(segments)))


def parse_revision(data: bytes) -> str:
    r"""
    Parse REPLY_REVISION data to the software revision, e.g. `PAACYS00-010-R01`.

    The data is `<software revision> <board type> <engine code>` (SSI guide table 2-73). The DS8178 pads the fields
    with several spaces and sends a binary engine code, e.g. `PAACYS00-010-R01   F \xff \x01\x01`, so only the first
    field is decoded.

    Raises:
      UnexpectedResponseError: If the reply does not start with a printable ASCII revision.
    """
    revision = data.split(b" ", 1)[0]
    if not revision or not revision.isascii() or not revision.decode("ascii").isprintable():
        msg = f"Expected a software revision string, received {data!r}."
        raise UnexpectedResponseError(msg)

    return revision.decode("ascii")


def parse_nak_cause(data: bytes) -> NakCause:
    """
    Parse the data of a CMD_NAK: one cause byte (SSI guide p. 2-25).

    Raises:
      UnexpectedResponseError: If the cause byte is missing.
    """
    if not data:
        msg = "Received a CMD_NAK without a cause."
        raise UnexpectedResponseError(msg)

    return NakCause(data[0])


def parse_event(data: bytes) -> EventCode:
    """
    Parse the data of an EVENT packet: one event code byte (SSI guide p. 2-41).

    Raises:
      UnexpectedResponseError: If the event code is missing.
    """
    if not data:
        msg = "Received an EVENT packet without an event code."
        raise UnexpectedResponseError(msg)

    return EventCode(data[0])


_PARAM_PAGES = {0xF0: 256, 0xF1: 512, 0xF2: 768}
_PARAM_EXTENDED = 0xF8
_TYPE_STRING = 0xF3
_TYPE_WORD = 0xF4
_TYPE_ARRAY = 0xF6
_TYPE_MULTIPACKET = 0xF7
_DATA_TYPES = (_TYPE_STRING, _TYPE_WORD, _TYPE_ARRAY, _TYPE_MULTIPACKET)


def encode_param_number(number: int) -> bytes:
    """
    Encode a parameter number for PARAM_REQUEST and PARAM_SEND (SSI guide tables 2-67 and 2-71).

    The PRG prints the result as "SSI #", e.g. parameter 238 is `EE`, 256 is `F0 00`, 1324 is `F8 05 2C`.

    Raises:
      ValueError: If the number falls in a gap the encoding cannot express, e.g. 240 to 255.
    """
    if 0 <= number <= 239:
        return bytes([number])
    for prefix, base in _PARAM_PAGES.items():
        if base <= number <= base + 239:
            return bytes([prefix, number - base])
    if number >= 1024:
        return bytes([_PARAM_EXTENDED]) + number.to_bytes(2, "big")

    msg = f"Parameter number {number} has no SSI encoding."
    raise ValueError(msg)


def encode_param_send(parameters: dict[int, int], beep: int = NO_BEEP) -> bytes:
    """
    Encode the data of a PARAM_SEND that sets byte-valued parameters.

    The data is `<beep code> (<param number> <value>)...` (SSI guide table 2-69).
    """
    return bytes([beep]) + b"".join(
        encode_param_number(number) + bytes([value]) for number, value in parameters.items()
    )


ALL_PARAMETERS = 0xFE
"""Parameter number requesting every supported parameter; only valid first in a PARAM_REQUEST (SSI guide p. 2-55)."""


def parse_param_request(data: bytes) -> list[int]:
    """
    Parse the parameter numbers of a PARAM_REQUEST, e.g. `EE 88` to `[238, 136]` (SSI guide table 2-67).

    A leading `FE` (`ALL_PARAMETERS`) is returned as is.

    Raises:
      UnexpectedResponseError: If the data ends in the middle of a parameter number.
    """
    numbers, position = [], 0
    try:
        while position < len(data):
            if position == 0 and data[0] == ALL_PARAMETERS:
                number, position = ALL_PARAMETERS, 1
            else:
                number, position = _read_param_number(data, position)
            numbers.append(number)
    except IndexError as error:
        msg = f"Received a truncated PARAM_REQUEST {data.hex(' ')}."
        raise UnexpectedResponseError(msg) from error

    return numbers


def parse_param_send(data: bytes) -> dict[int, int | bytes]:
    """
    Parse the data of a PARAM_SEND reply to a PARAM_REQUEST (SSI guide tables 2-68 and 2-72).

    The data is a beep code followed by parameters, each either `<number> <byte>` or prefixed with a data type:
    word `F4` (returned as int), string `F3`, array `F6` or multipacket `F7` (returned as bytes). Multipacket
    fragments carry an offset and are placed into the value at that offset. Unsupported parameters are simply absent.

    Raises:
      UnexpectedResponseError: If the data ends in the middle of a parameter.
    """
    values: dict[int, int | bytes] = {}
    position = 1
    try:
        while position < len(data):
            data_type = data[position] if data[position] in _DATA_TYPES else None
            if data_type is not None:
                position += 1
            number, position = _read_param_number(data, position)

            if data_type is None:
                values[number], position = _read(data, position, 1)[0], position + 1
            elif data_type == _TYPE_WORD:
                values[number], position = int.from_bytes(_read(data, position, 2), "big"), position + 2
            elif data_type == _TYPE_MULTIPACKET:
                length, offset = data[position], int.from_bytes(_read(data, position + 1, 2), "big")
                fragment = _read(data, position + 3, length)
                previous = values.get(number, b"")
                values[number] = (previous if isinstance(previous, bytes) else b"")[:offset] + fragment
                position += 3 + length
            else:
                length = data[position]
                values[number], position = _read(data, position + 1, length), position + 1 + length
    except IndexError as error:
        msg = f"Received a truncated PARAM_SEND {data.hex(' ')}."
        raise UnexpectedResponseError(msg) from error

    return values


def _read_param_number(data: bytes, position: int) -> tuple[int, int]:
    prefix = data[position]
    if prefix in _PARAM_PAGES:
        return _PARAM_PAGES[prefix] + _read(data, position + 1, 1)[0], position + 2
    if prefix == _PARAM_EXTENDED:
        return int.from_bytes(_read(data, position + 1, 2), "big"), position + 3

    return prefix, position + 1


def _read(data: bytes, position: int, length: int) -> bytes:
    chunk = data[position : position + length]
    if len(chunk) != length:
        raise IndexError(position)

    return chunk


def _decode_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
