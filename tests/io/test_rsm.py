"""RSM attribute request encoding and reply parsing, from replies captured from a CR8178 cradle."""

import pytest

from unitelabs.zebra_ds8178.io.errors import UnexpectedResponseError
from unitelabs.zebra_ds8178.io.rsm import encode_attribute_get, encode_get_packet_size, parse_attribute_get


def frame(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


MODEL = "00 21 02 00 02 15 53 01 00 13 00 00 43 52 38 31 37 38 2D 53 43 31 30 30 46 34 57 57 20 20 00 FF FF"
SERIAL = "00 1F 02 00 02 16 53 01 00 11 00 00 32 35 33 34 34 30 31 30 35 35 31 39 33 38 20 20 00 FF FF"
DATE = "00 16 02 00 02 17 53 01 00 08 00 00 31 30 44 45 43 32 35 00 FF FF"


def test_encodes_the_get_packet_size_command_from_the_prg() -> None:
    """The bytes match the PRG p. 9-9 example, which the cradle answered as documented."""
    assert encode_get_packet_size() == frame("00 06 20 00 FF FF")


@pytest.mark.parametrize(
    ("attributes", "encoded"),
    [
        ((10061,), "00 08 02 00 27 4D 42 00"),  # PRG p. 9-9
        ((533,), "00 08 02 00 02 15 42 00"),
        ((533, 534), "00 0A 02 00 02 15 02 16 42 00"),
    ],
)
def test_encodes_attribute_gets(attributes: tuple[int, ...], encoded: str) -> None:
    """Requests carry their total length, the attribute numbers and the PRG example's trailer."""
    assert encode_attribute_get(*attributes) == frame(encoded)


@pytest.mark.parametrize(
    ("reply", "values"),
    [
        (MODEL, {533: "CR8178-SC100F4WW"}),
        (SERIAL, {534: "25344010551938"}),
        (DATE, {535: "10DEC25"}),
        ("00 06 02 00 FF FF", {}),
        ("00 15 02 00 02 1D 41 01 42 00 06 00 00 C4 7D CC 86 F2 09 FF FF", {541: frame("C4 7D CC 86 F2 09")}),
        ("00 0B 02 00 00 88 42 07 63 FF FF", {136: 99}),
        (
            "00 1D 02 00 27 4D 41 01 42 00 0E 00 00 00 00 01 03 02 03 03 03 04 03 05 03 06 03 FF FF",  # PRG p. 9-9
            {10061: frame("00 00 01 03 02 03 03 03 04 03 05 03 06 03")},
        ),
    ],
)
def test_parses_captured_attribute_replies(reply: str, values: dict) -> None:
    """Strings lose their NUL and padding, arrays stay bytes, bytes become int; unsupported ones are absent."""
    assert parse_attribute_get(frame(reply)) == values


def test_parses_several_attributes_in_one_reply() -> None:
    """The cradle answers a two-attribute request with both values back to back."""
    reply = (
        "00 3A 02 00 02 15 53 01 00 13 00 00 43 52 38 31 37 38 2D 53 43 31 30 30 46 34 57 57 20 20 00 "
        "02 16 53 01 00 11 00 00 32 35 33 34 34 30 31 30 35 35 31 39 33 38 20 20 00 FF FF"
    )

    assert parse_attribute_get(frame(reply)) == {533: "CR8178-SC100F4WW", 534: "25344010551938"}


@pytest.mark.parametrize(
    "reply",
    [
        "00 07 02 00 FF FF",  # length field disagrees with the data
        "00 08 20 00 00 F0 00 F0",  # a packet size reply, not an attribute reply
        "00 0C 02 00 02 15 53 01 00 13 FF FF",  # string cut short
        "00 0B 02 00 00 88 58 07 00 FF FF",  # 'X' (action) attributes cannot be read
    ],
)
def test_rejects_malformed_replies(reply: str) -> None:
    """Anything that does not fit the observed layout is reported, never half-parsed."""
    with pytest.raises(UnexpectedResponseError):
        parse_attribute_get(frame(reply))
