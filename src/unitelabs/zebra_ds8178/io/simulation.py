import collections.abc
import functools
import itertools

from unitelabs.bus import TransportFactory
from unitelabs.bus.testing import create_mock_connection

from .rsm import ATTRIBUTE_GET, GET_PACKET_SIZE, Attribute
from .ssi import (
    ALL_PARAMETERS,
    BeepCode,
    NakCause,
    Opcode,
    Packet,
    Source,
    encode_param_number,
    parse_param_request,
    parse_param_send,
)

BEEP_CODES = frozenset(code.value for code in BeepCode)

SAMPLE_BARCODES = (
    (0x08, b"012345678905"),  # UPC-A
    (0x09, b"04252614"),  # UPC-E
    (0x0A, b"31234566"),  # EAN-8
    (0x0B, b"3456789012340"),  # EAN-13
    (0x03, b"1234567890"),  # Code 128
    (0x0F, b"0194019097685457131701193017"),  # GS1-128
)
"""SSI bar code type and data of the PRG Appendix F samples, as a DS8178 decoded them."""

REVISION = b"PAACYS00-010-R01   F \xff \x01\x01"
"""REPLY_REVISION data in the format a CR8178 cradle sends it."""

CRADLE_ATTRIBUTES: dict[int, str] = {
    Attribute.MODEL_NUMBER: "CR8178-SC100F4WW",
    Attribute.SERIAL_NUMBER: "SIMULATED",
    Attribute.DATE_OF_MANUFACTURE: "01JAN25",
}

DEFAULT_PARAMETERS = {238: 1, 136: 99, 138: 0, 56: 1, 145: 1}
"""
Packeted decode data, 9.9 s Decode Session Timeout and Level trigger mode, as read from a DS8178, and the PRG
defaults for Beep After Good Decode (on) and Beeper Tone (medium).
"""


class SimulatedScanner:
    """
    A simulated DS8178 on its cradle that answers SSI packets over a mock connection.

    A host scan decodes the next Appendix F sample bar code at once, unless `barcode_present` is false, in which case
    scans time out. Parameters, the scanning enabled state and the cradle's attributes behave as on the hardware; like
    the real scanner, it refuses START_DECODE as DENIED while scanning is disabled. Trigger pulls are not simulated:
    the mock connection only answers requests, so nothing reaches decode subscribers unasked.

    Args:
      barcode_present: Whether a bar code is in front of the scanner for host scans.
    """

    def __init__(self, barcode_present: bool = True) -> None:
        self.barcode_present = barcode_present
        self.parameters: dict[int, int] = dict(DEFAULT_PARAMETERS)
        self.scanning_enabled = True
        self._samples = itertools.cycle(SAMPLE_BARCODES)

    def connection_factory(self) -> TransportFactory:
        """Create a factory for a mock connection that this simulated scanner answers."""
        return functools.partial(create_mock_connection, stubs=[self._answer])

    def _answer(self, request: bytes) -> collections.abc.Generator[bytes, None, None]:
        packet = Packet.decode(request)
        for reply in self._reply(packet):
            yield Packet(reply.opcode, reply.data, source=Source.DECODER).encode()

    def _reply(self, packet: Packet) -> list[Packet]:
        ack, denied = Packet(Opcode.CMD_ACK), Packet(Opcode.CMD_NAK, bytes([NakCause.DENIED]))
        match packet.opcode:
            case Opcode.CMD_ACK | Opcode.CMD_NAK:
                return []  # never answered (SSI guide p. 2-20)
            case Opcode.REQUEST_REVISION:
                return [Packet(Opcode.REPLY_REVISION, REVISION)]
            case Opcode.PARAM_REQUEST:
                return [Packet(Opcode.PARAM_SEND, self._read_parameters(packet.data))]
            case Opcode.PARAM_SEND:
                self.parameters.update({number: int(value) for number, value in parse_param_send(packet.data).items()})
                return [ack]
            case Opcode.BEEP:
                return [ack] if packet.data and packet.data[0] in BEEP_CODES else [denied]
            case Opcode.AIM_ON | Opcode.AIM_OFF | Opcode.STOP_DECODE:
                return [ack]
            case Opcode.SCAN_ENABLE | Opcode.SCAN_DISABLE:
                self.scanning_enabled = packet.opcode == Opcode.SCAN_ENABLE
                return [ack]
            case Opcode.START_DECODE:
                if not self.scanning_enabled:
                    return [denied]
                if not self.barcode_present:
                    return [ack]
                symbology, data = next(self._samples)
                return [ack, Packet(Opcode.DECODE_DATA, bytes([symbology]) + data)]
            case Opcode.SSI_MGMT_COMMAND:
                return [Packet(Opcode.SSI_MGMT_COMMAND, self._manage(packet.data))]
            case _:
                return [Packet(Opcode.CMD_NAK, bytes([NakCause.BAD_CONTEXT]))]

    def _read_parameters(self, request: bytes) -> bytes:
        """Answer only supported parameters, as the scanner does (SSI guide p. 2-58); byte values only."""
        requested = parse_param_request(request)
        numbers = list(self.parameters) if requested[:1] == [ALL_PARAMETERS] else requested
        values = b"".join(
            encode_param_number(number) + bytes([self.parameters[number]])
            for number in numbers
            if number in self.parameters
        )
        return b"\xff" + values

    @staticmethod
    def _manage(request: bytes) -> bytes:
        """Answer the RSM Get Packet Size and ATTRIBUTE_GET commands as the CR8178 cradle does."""
        if request[2] == GET_PACKET_SIZE:
            return bytes.fromhex("00 08 20 00 00 F0 00 F0")

        body = b""
        for position in range(4, len(request) - 2, 2):
            attribute = int.from_bytes(request[position : position + 2], "big")
            if (text := CRADLE_ATTRIBUTES.get(attribute)) is not None:
                value = text.encode() + b"\0"
                body += attribute.to_bytes(2, "big") + b"S\x01" + len(value).to_bytes(2, "big") + b"\0\0" + value
        reply = bytes([ATTRIBUTE_GET, 0x00]) + body + b"\xff\xff"
        return (len(reply) + 2).to_bytes(2, "big") + reply
