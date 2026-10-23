"""Packet encoding and decoding of the Simple Serial Interface, independent of hardware."""

import pytest

from unitelabs.zebra_ds8178.io.errors import UnexpectedResponseError
from unitelabs.zebra_ds8178.io.ssi import (
    BeepCode,
    EventCode,
    NakCause,
    Opcode,
    Packet,
    Source,
    Status,
    Symbology,
    encode_param_number,
    encode_param_send,
    join_message,
    parse_param_request,
    parse_decode_data,
    parse_event,
    parse_nak_cause,
    parse_param_send,
    parse_revision,
)


def frame(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


# Host packets captured from a DS8178 on its CR8178 cradle (ssi_test.py / set_packeted.py).
@pytest.mark.parametrize(
    ("packet", "wire"),
    [
        (Packet(Opcode.REQUEST_REVISION), "04 A3 04 00 FF 55"),
        (Packet(Opcode.CMD_ACK), "04 D0 04 00 FF 28"),
        (Packet(Opcode.BEEP, bytes([BeepCode.SHORT_HIGH_1])), "05 E6 04 00 00 FF 11"),
        (Packet(Opcode.START_DECODE), "04 E4 04 00 FF 14"),
        (Packet(Opcode.PARAM_SEND, encode_param_send({238: 1}), status=Status.PERMANENT), "07 C6 04 08 FF EE 01 FD 39"),
        # SSI guide table 2-68, request for parameters 1 and 0x9C.
        (Packet(Opcode.PARAM_REQUEST, b"\x01\x9c"), "06 C7 04 00 01 9C FE 92"),
    ],
)
def test_encodes_host_packets(packet, wire) -> None:
    """Host packets match the bytes captured from the scanner and the SSI guide."""
    assert packet.encode() == frame(wire)


def test_decodes_a_captured_revision_reply() -> None:
    """A real REPLY_REVISION decodes to its software revision."""
    packet = Packet.decode(
        frame("1D A4 00 80 50 41 41 43 59 53 30 30 2D 30 31 30 2D 52 30 31 20 20 20 46 20 FF 20 01 01 F9 19")
    )

    assert packet.opcode == Opcode.REPLY_REVISION
    assert packet.source == Source.DECODER
    assert packet.status == Status.NONE  # the DS8178 sets the undocumented bit 7; it is masked off
    assert parse_revision(packet.data) == "PAACYS00-010-R01"


def test_decodes_a_captured_ack() -> None:
    """A real decoder CMD_ACK decodes with an empty payload."""
    packet = Packet.decode(frame("04 D0 00 80 FE AC"))

    assert packet.opcode == Opcode.CMD_ACK
    assert packet.data == b""


def test_round_trips_a_multipacket_status() -> None:
    """Encoding then decoding keeps opcode, source, status and data."""
    packet = Packet(Opcode.DECODE_DATA, b"\x03abc", source=Source.DECODER, status=Status.CONTINUATION)

    assert Packet.decode(packet.encode()) == packet
    assert packet.is_continued


def test_rejects_a_wrong_checksum() -> None:
    """A corrupted checksum is reported, not silently accepted."""
    with pytest.raises(UnexpectedResponseError, match="checksum"):
        Packet.decode(frame("04 D0 00 80 FE AD"))


@pytest.mark.parametrize("wire", ["04 D0 00", "05 D0 00 80 FE AC"])
def test_rejects_a_frame_of_the_wrong_length(wire) -> None:
    """A frame shorter or longer than its length byte is rejected."""
    with pytest.raises(UnexpectedResponseError, match="malformed"):
        Packet.decode(frame(wire))


# The Appendix F sample bar codes scanned on hardware.
@pytest.mark.parametrize(
    ("data", "symbology", "text"),
    [
        (b"\x031234567890", Symbology.CODE_128, "1234567890"),
        (b"\x08012345678905", Symbology.UPC_A, "012345678905"),
        (b"\x0904252614", Symbology.UPC_E, "04252614"),
        (b"\x0a31234566", Symbology.EAN_8, "31234566"),
        (b"\x0b3456789012340", Symbology.EAN_13, "3456789012340"),
        (b"\x0f0194019097685457131701193017", Symbology.GS1_128, "0194019097685457131701193017"),
    ],
)
def test_parses_decode_data(data, symbology, text) -> None:
    """Each Appendix F sample bar code decodes to its symbology and text."""
    decoded = parse_decode_data(data)

    assert decoded.symbology == symbology
    assert decoded.data == text


def test_parses_multipacket_decode_data() -> None:
    """Multipacket-format decode data yields the embedded type and joined segments."""
    # SSI guide table 2-36: Micro PDF417 data "ABC" and "DEFG" in two segments.
    decoded = parse_decode_data(frame("99 1A 02 00 00 03") + b"ABC" + frame("00 00 04") + b"DEFG")

    assert decoded.symbology == Symbology.MICRO_PDF417
    assert decoded.data == "ABCDEFG"


@pytest.mark.parametrize("data", [b"", frame("99 1A"), frame("99 1A 01 00 00 05") + b"AB"])
def test_rejects_truncated_decode_data(data) -> None:
    """Decode data cut short is rejected."""
    with pytest.raises(UnexpectedResponseError):
        parse_decode_data(data)


def test_maps_both_matrix_2_of_5_ids_and_unknown_types() -> None:
    """Both documented Matrix 2 of 5 IDs map to one member; unlisted IDs are unknown."""
    assert Symbology(0x39) is Symbology(0x71) is Symbology.MATRIX_2_OF_5
    assert Symbology(0xFE) is Symbology.UNKNOWN


@pytest.mark.parametrize(
    ("number", "encoded"),
    [(0, "00"), (238, "EE"), (256, "F0 00"), (334, "F0 4E"), (607, "F1 5F"), (859, "F2 5B"), (1324, "F8 05 2C")],
)
def test_encodes_parameter_numbers_as_the_prg_prints_them(number, encoded) -> None:
    """Parameter numbers encode to the 'SSI #' the PRG prints."""
    assert encode_param_number(number) == frame(encoded)


@pytest.mark.parametrize("number", [240, 255, 1008, -1])
def test_rejects_parameter_numbers_without_an_encoding(number) -> None:
    """Numbers in the encoding's gaps are refused."""
    with pytest.raises(ValueError, match="no SSI encoding"):
        encode_param_number(number)


# Replies from SSI guide table 2-68; decoding them also checks the published checksums.
@pytest.mark.parametrize(
    ("wire", "values"),
    [
        ("0D C6 00 00 FF 01 00 02 01 9C 07 E6 63 FC 3E", {1: 0x00, 2: 0x01, 0x9C: 0x07, 0xE6: 0x63}),
        ("05 C6 00 00 FF FE 36", {}),
        ("0A C6 00 00 FF F4 F0 3E 04 FF FB 0C", {318: 1279}),
        ("0B C6 00 00 FF F4 F8 04 5E 00 00 FB E2", {1118: 0}),
        (
            "1D C6 00 00 FF F7 F1 15 12 00 00 44 53 34 33 30 38 2D 53 52 30 30 30 30 37 5A 5A 57 57 F7 7E",
            {533: b"DS4308-SR00007ZZWW"},
        ),
    ],
)
def test_parses_parameter_replies(wire, values) -> None:
    """The SSI guide's example PARAM_SEND replies decode to their values."""
    packet = Packet.decode(frame(wire))

    assert packet.opcode == Opcode.PARAM_SEND
    assert parse_param_send(packet.data) == values


def test_places_multipacket_parameter_fragments_at_their_offset() -> None:
    """Multipacket parameter fragments are reassembled by offset."""
    data = frame("FF F7 F1 15 02 00 00") + b"DS" + frame("F7 F1 15 02 00 02") + b"81"

    assert parse_param_send(data) == {533: b"DS81"}


def test_parses_string_and_array_parameters() -> None:
    """String and array parameters decode to their bytes."""
    data = frame("FF F3 F1 16 03") + b"M1J" + frame("F6 F2 1D 02 05 27")

    assert parse_param_send(data) == {534: b"M1J", 797: b"\x05\x27"}


def test_rejects_a_truncated_parameter_reply() -> None:
    """A parameter reply cut short is rejected."""
    with pytest.raises(UnexpectedResponseError, match="truncated"):
        parse_param_send(frame("FF F4 F0 3E 04"))


def test_parses_nak_causes_and_events() -> None:
    """Known causes and events map to members; unlisted ones are unknown."""
    assert parse_nak_cause(b"\x06") is NakCause.DENIED
    assert parse_nak_cause(b"\x03") is NakCause.UNKNOWN
    assert parse_event(b"\x03") is EventCode.BOOT_UP
    assert parse_event(b"\x02") is EventCode.UNKNOWN


@pytest.mark.parametrize("parse", [parse_nak_cause, parse_event, parse_revision])
def test_rejects_empty_payloads(parse) -> None:
    """Packets missing their one required byte are rejected."""
    with pytest.raises(UnexpectedResponseError):
        parse(b"")


@pytest.mark.parametrize(
    ("opcode", "parts", "joined"),
    [
        (Opcode.DECODE_DATA, [b"\x1bAB", b"\x1bCD"], b"\x1bABCD"),
        (Opcode.PARAM_SEND, [b"\xff\x01\x00", b"\xff\x02\x01"], b"\xff\x01\x00\x02\x01"),
        (Opcode.SSI_MGMT_COMMAND, [b"\x00\x01", b"\x02\x03"], b"\x00\x01\x02\x03"),
    ],
)
def test_joins_multipacket_messages(opcode, parts, joined) -> None:
    """The byte DECODE_DATA and PARAM_SEND repeat per packet is kept once; other messages are concatenated."""
    packets = [Packet(opcode, part, source=Source.DECODER, status=Status.CONTINUATION) for part in parts[:-1]]
    packets.append(Packet(opcode, parts[-1], source=Source.DECODER))

    assert join_message(packets) == Packet(opcode, joined, source=Source.DECODER)


@pytest.mark.parametrize(
    ("data", "numbers"),
    [("EE 88", [238, 136]), ("F0 4E F8 05 2C", [334, 1324]), ("FE", [0xFE]), ("FE 01", [0xFE, 1])],
)
def test_parses_parameter_request_numbers(data: str, numbers: list[int]) -> None:
    """Request numbers decode with the same prefixes as replies; a leading FE asks for all parameters."""
    assert parse_param_request(frame(data)) == numbers


def test_rejects_a_truncated_parameter_request() -> None:
    """A page prefix without its number is reported."""
    with pytest.raises(UnexpectedResponseError, match="truncated"):
        parse_param_request(frame("EE F0"))
