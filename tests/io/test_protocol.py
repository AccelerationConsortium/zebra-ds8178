"""Protocol core: connecting, request/reply with retries, and routing of unsolicited packets, against a stub scanner."""

import asyncio
import collections.abc
import datetime
import functools
import types

import pytest

from unitelabs.bus.testing import create_mock_connection
from unitelabs.zebra_ds8178.io import (
    CommandRejectedError,
    NoBarcodeDecodedError,
    ScannerBusyError,
    ScanningDisabledError,
    ScanStoppedError,
    ScannerNotRespondingError,
    SerialPortNotFoundError,
    UnexpectedResponseError,
    ZebraDs8178Protocol,
)
from unitelabs.zebra_ds8178.io import protocol as protocol_module
from unitelabs.zebra_ds8178.io import ssi
from unitelabs.zebra_ds8178.io.ssi import BeepCode, BeeperTone, NakCause, Opcode, Packet, Source, Status

# Captured from a DS8178: REPLY_REVISION and a decoder CMD_ACK (the scanner sets the undocumented status bit 7).
REVISION_REPLY = bytes.fromhex(
    "1D A4 00 80 50 41 41 43 59 53 30 30 2D 30 31 30 2D 52 30 31 20 20 20 46 20 FF 20 01 01 F9 19"
)
ACK = bytes.fromhex("04 D0 00 80 FE AC")
PACKETED = Packet(Opcode.PARAM_SEND, bytes.fromhex("FF EE 01"), source=Source.DECODER).encode()


def decoder(opcode: Opcode, data: bytes = b"", status: Status = Status.NONE) -> bytes:
    return Packet(opcode, data, source=Source.DECODER, status=status).encode()


def nak(cause: NakCause) -> bytes:
    return decoder(Opcode.CMD_NAK, bytes([cause]))


class FakeScanner:
    """
    Answer each request opcode with the next of its queued replies and record every packet the host sends.

    The last queued reply repeats; `None` stays silent and a list is sent in chunks. Connecting uses the first
    REQUEST_REVISION and PARAM_REQUEST replies, so tests overriding those queue the connection reply first.
    """

    def __init__(self, replies: dict[Opcode, list[bytes | list[bytes] | None]] | None = None) -> None:
        self.replies = {
            Opcode.REQUEST_REVISION: [REVISION_REPLY],
            Opcode.PARAM_REQUEST: [PACKETED],
            Opcode.SCAN_ENABLE: [ACK],
            **(replies or {}),
        }
        self.received: list[Packet] = []

    def __call__(self, request: bytes) -> collections.abc.Generator[bytes, None, None]:
        packet = Packet.decode(request)
        self.received.append(packet)
        queued = self.replies.get(Opcode(packet.opcode), [])
        reply = queued.pop(0) if len(queued) > 1 else (queued[0] if queued else None)
        if reply is not None:
            yield from reply if isinstance(reply, list) else [reply]

    def opcodes(self) -> list[int]:
        return [packet.opcode for packet in self.received]


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssi, "RESPONSE_TIMEOUT", 0.05)
    monkeypatch.setattr(protocol_module, "OPEN_TIMEOUT", 0.5)
    monkeypatch.setattr(protocol_module, "CHARACTER_TIMEOUT", 0.05)


@pytest.fixture
async def connect() -> collections.abc.AsyncGenerator[collections.abc.Callable, None]:
    """Open a protocol to a `FakeScanner` through the mock transport."""
    opened: list[ZebraDs8178Protocol] = []

    async def _connect(scanner: FakeScanner) -> ZebraDs8178Protocol:
        protocol = ZebraDs8178Protocol(port="mock")
        await protocol._reopen(functools.partial(create_mock_connection, stubs=[scanner]), {})
        opened.append(protocol)
        scanner.received.clear()
        return protocol

    yield _connect
    for protocol in opened:
        protocol.close()


async def settle() -> None:
    """Let the mock transport deliver queued data, which it schedules on the event loop."""
    for _ in range(5):
        await asyncio.sleep(0)


# - Connecting ------------------------------------------------------------------------------------------------------


async def test_connecting_confirms_ssi_keeps_an_already_packeted_format_and_enables_scanning() -> None:
    """With packeted decode data already set, nothing is written to non-volatile memory; scanning is enabled."""
    scanner = FakeScanner()
    protocol = ZebraDs8178Protocol(port="mock")

    await protocol._reopen(functools.partial(create_mock_connection, stubs=[scanner]), {})

    assert scanner.opcodes() == [Opcode.REQUEST_REVISION, Opcode.PARAM_REQUEST, Opcode.SCAN_ENABLE]
    assert protocol.scanning_enabled
    assert scanner.received[1].data == b"\xee"
    protocol.close()


async def test_connecting_switches_raw_decode_data_to_packeted_permanently() -> None:
    """A scanner left in raw mode is switched to packeted mode with the permanent bit set and no beep."""
    scanner = FakeScanner(
        {Opcode.PARAM_REQUEST: [decoder(Opcode.PARAM_SEND, bytes.fromhex("FF EE 00"))], Opcode.PARAM_SEND: [ACK]}
    )
    protocol = ZebraDs8178Protocol(port="mock")

    await protocol._reopen(functools.partial(create_mock_connection, stubs=[scanner]), {})

    assert scanner.received[-2] == Packet(Opcode.PARAM_SEND, bytes.fromhex("FF EE 01"), status=Status.PERMANENT)
    protocol.close()


async def test_open_device_reports_a_silent_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A port that opens but never answers ends in ScannerNotRespondingError, not a hang."""
    monkeypatch.setattr(protocol_module, "create_serial_connection", functools.partial(create_mock_connection))
    protocol = ZebraDs8178Protocol(port="/dev/ttyACM0")

    with pytest.raises(ScannerNotRespondingError, match="/dev/ttyACM0"):
        await protocol.open_device()


async def test_open_device_reports_a_port_that_cannot_be_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OS error opening the port becomes SerialPortNotFoundError with the port in the message."""

    async def unavailable(*args, **kwargs):
        raise FileNotFoundError("No such file or directory")

    monkeypatch.setattr(protocol_module, "create_serial_connection", unavailable)
    protocol = ZebraDs8178Protocol(port="/dev/ttyACM9")

    with pytest.raises(SerialPortNotFoundError, match="/dev/ttyACM9"):
        await protocol.open_device()


@pytest.mark.parametrize(
    ("ports", "found"),
    [([], "none"), ([("/dev/cu.usbmodem1", 0x05E0), ("/dev/cu.usbmodem2", 0x05E0)], "usbmodem1, /dev/cu.usbmodem2")],
)
def test_cradle_detection_needs_exactly_one_zebra_port(monkeypatch: pytest.MonkeyPatch, ports, found) -> None:
    """Zero or several Zebra ports cannot be resolved automatically; the message lists what was found."""
    listed = [types.SimpleNamespace(device=device, vid=vid) for device, vid in ports]
    monkeypatch.setattr(protocol_module.serial.tools.list_ports, "comports", lambda: listed)

    with pytest.raises(SerialPortNotFoundError, match=found):
        ZebraDs8178Protocol._find_cradle_port()


def test_cradle_detection_ignores_other_vendors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the port with Zebra's USB vendor ID is picked."""
    listed = [
        types.SimpleNamespace(device="/dev/cu.usbserial", vid=0x0403),
        types.SimpleNamespace(device="/dev/cu.usbmodem1201", vid=0x05E0),
        types.SimpleNamespace(device="/dev/cu.Bluetooth", vid=None),
    ]
    monkeypatch.setattr(protocol_module.serial.tools.list_ports, "comports", lambda: listed)

    assert ZebraDs8178Protocol._find_cradle_port() == "/dev/cu.usbmodem1201"


# - Requests --------------------------------------------------------------------------------------------------------


async def test_get_revision(connect) -> None:
    """The captured REPLY_REVISION decodes to the scanner's software revision."""
    protocol = await connect(FakeScanner())

    assert await protocol.get_revision() == "PAACYS00-010-R01"


async def test_a_reply_split_across_reads_is_reassembled(connect) -> None:
    """A packet arriving in several chunks completes the request once its last byte is in."""
    protocol = await connect(FakeScanner({Opcode.REQUEST_REVISION: [[REVISION_REPLY[:7], REVISION_REPLY[7:]]]}))

    assert await protocol.get_revision() == "PAACYS00-010-R01"


async def test_a_multipacket_reply_is_joined_without_its_repeated_beep_code(connect) -> None:
    """PARAM_SEND repeats the beep code in every packet; the parameters of both packets are returned."""
    first = decoder(Opcode.PARAM_SEND, bytes.fromhex("FF 01 00"), status=Status.CONTINUATION)
    last = decoder(Opcode.PARAM_SEND, bytes.fromhex("FF 9C 07"))
    protocol = await connect(FakeScanner({Opcode.PARAM_REQUEST: [PACKETED, [first, last]]}))

    assert await protocol.get_parameters(1, 0x9C) == {1: 0, 0x9C: 7}


async def test_set_parameters_sends_a_temporary_change_by_default(connect) -> None:
    """Without `permanent`, the change type bit stays clear so the scanner forgets it on power-down."""
    scanner = FakeScanner({Opcode.PARAM_SEND: [ACK]})
    protocol = await connect(scanner)

    await protocol.set_parameters({136: 50})

    assert scanner.received == [Packet(Opcode.PARAM_SEND, bytes.fromhex("FF 88 32"))]


async def test_a_lost_reply_is_resent_with_the_retransmit_bit(connect) -> None:
    """After a timeout the same request goes out again, marked as a retransmission (SSI guide p. 2-21)."""
    scanner = FakeScanner({Opcode.BEEP: [None, ACK]})
    protocol = await connect(scanner)

    await protocol.request(Packet(Opcode.BEEP, b"\x00"))

    assert [packet.status for packet in scanner.received] == [Status.NONE, Status.RETRANSMIT]


async def test_a_resend_nak_is_retried(connect) -> None:
    """A checksum NAK from the scanner triggers a resend instead of an error."""
    scanner = FakeScanner({Opcode.BEEP: [nak(NakCause.RESEND), ACK]})
    protocol = await connect(scanner)

    await protocol.request(Packet(Opcode.BEEP, b"\x00"))

    assert len(scanner.received) == 2


@pytest.mark.parametrize("cause", [NakCause.DENIED, NakCause.BAD_CONTEXT])
async def test_a_refusal_is_raised_without_retrying(connect, cause: NakCause) -> None:
    """A denied or unknown request is not resent; the scanner's reason is in the message."""
    scanner = FakeScanner({Opcode.BEEP: [nak(cause)]})
    protocol = await connect(scanner)

    with pytest.raises(CommandRejectedError, match=f"BEEP.*{cause.name}"):
        await protocol.request(Packet(Opcode.BEEP, b"\x7f"))
    assert len(scanner.received) == 1


async def test_no_reply_after_all_retries_is_not_responding(connect) -> None:
    """The initial send plus two resends go unanswered, then the scanner is reported as not responding."""
    scanner = FakeScanner()
    protocol = await connect(scanner)

    with pytest.raises(ScannerNotRespondingError, match="BEEP after 3 attempts"):
        await protocol.request(Packet(Opcode.BEEP, b"\x00"))
    assert len(scanner.received) == 3


async def test_a_reply_with_the_wrong_opcode_is_unexpected(connect) -> None:
    """An ACK where a REPLY_REVISION was expected is reported rather than parsed as a revision."""
    protocol = await connect(FakeScanner({Opcode.REQUEST_REVISION: [REVISION_REPLY, ACK]}))

    with pytest.raises(UnexpectedResponseError, match="Expected REPLY_REVISION.*received CMD_ACK"):
        await protocol.get_revision()


async def test_a_closed_connection_is_not_responding(connect) -> None:
    """Requests after the connection closed point the operator to Start Real Mode."""
    protocol = await connect(FakeScanner())
    protocol.close()
    await settle()

    with pytest.raises(ScannerNotRespondingError, match="Start Real Mode"):
        await protocol.get_revision()


# - Indicators and scanning state -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "packet"),
    [
        (lambda protocol: protocol.beep(BeepCode.SHORT_HIGH_1), Packet(Opcode.BEEP, b"\x00")),
        (lambda protocol: protocol.beep(BeepCode.LOW_CLICK), Packet(Opcode.BEEP, b"\x1d")),
        (lambda protocol: protocol.aim_on(), Packet(Opcode.AIM_ON)),
        (lambda protocol: protocol.aim_off(), Packet(Opcode.AIM_OFF)),
        (lambda protocol: protocol.scan_enable(), Packet(Opcode.SCAN_ENABLE)),
        (lambda protocol: protocol.scan_disable(), Packet(Opcode.SCAN_DISABLE)),
    ],
)
async def test_commands_send_their_ssi_packet_and_complete_on_ack(connect, call, packet: Packet) -> None:
    """Each command sends the packet the SSI guide defines and returns once the scanner ACKs it."""
    scanner = FakeScanner({Opcode(packet.opcode): [ACK]})
    protocol = await connect(scanner)

    await call(protocol)

    assert scanner.received == [packet]


async def test_scanning_state_follows_enable_and_disable(connect) -> None:
    """The tracked state changes only once the scanner has acknowledged the request."""
    scanner = FakeScanner({Opcode.SCAN_DISABLE: [ACK, nak(NakCause.DENIED)], Opcode.SCAN_ENABLE: [ACK]})
    protocol = await connect(scanner)

    await protocol.scan_disable()
    assert not protocol.scanning_enabled

    await protocol.scan_enable()
    assert protocol.scanning_enabled

    with pytest.raises(CommandRejectedError):
        await protocol.scan_disable()
    assert protocol.scanning_enabled


async def test_a_refused_aim_is_reported(connect) -> None:
    """Decoders without an aim pattern NAK it as denied (SSI guide p. 2-8)."""
    protocol = await connect(FakeScanner({Opcode.AIM_ON: [nak(NakCause.DENIED)]}))

    with pytest.raises(CommandRejectedError, match="AIM_ON.*DENIED"):
        await protocol.aim_on()


# - Beeper settings ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("call", "data"),
    [
        (lambda protocol: protocol.set_beep_after_good_decode(enabled=False), "FF 38 00"),
        (lambda protocol: protocol.set_beep_after_good_decode(enabled=True), "FF 38 01"),
        (lambda protocol: protocol.set_beeper_tone(BeeperTone.OFF), "FF 91 03"),
        (lambda protocol: protocol.set_beeper_tone(BeeperTone.MEDIUM), "FF 91 01"),
    ],
)
async def test_beeper_settings_are_written_temporarily_without_a_beep(connect, call, data: str) -> None:
    """Parameters 56 and 145 go out with the permanent bit clear, so nothing wears the scanner's memory."""
    scanner = FakeScanner({Opcode.PARAM_SEND: [ACK]})
    protocol = await connect(scanner)

    await call(protocol)

    assert scanner.received == [Packet(Opcode.PARAM_SEND, bytes.fromhex(data), status=Status.NONE)]


async def test_beeper_settings_are_read_back_from_the_scanner(connect) -> None:
    """The values come from PARAM_REQUEST, not from what the connector last wrote."""
    replies = [PACKETED, decoder(Opcode.PARAM_SEND, bytes.fromhex("FF 38 00")), decoder(Opcode.PARAM_SEND, bytes.fromhex("FF 91 03"))]
    protocol = await connect(FakeScanner({Opcode.PARAM_REQUEST: replies}))

    assert await protocol.get_beep_after_good_decode() is False
    assert await protocol.get_beeper_tone() is BeeperTone.OFF


@pytest.mark.parametrize(("reply", "match"), [("FF 91 07", "tone 7"), ("FF", "parameter 145")])
async def test_an_unknown_or_missing_tone_is_unexpected(connect, reply: str, match: str) -> None:
    """A value the PRG does not list, or no value at all, is reported instead of guessed."""
    protocol = await connect(FakeScanner({Opcode.PARAM_REQUEST: [PACKETED, decoder(Opcode.PARAM_SEND, bytes.fromhex(reply))]}))

    with pytest.raises(UnexpectedResponseError, match=match):
        await protocol.get_beeper_tone()


# - Scanning --------------------------------------------------------------------------------------------------------

UPC_E = decoder(Opcode.DECODE_DATA, b"\x0904252614")


async def test_scan_returns_the_bar_code_decoded_after_the_session_starts(connect) -> None:
    """START_DECODE is ACKed, then the decode data arrives and is returned parsed; no STOP_DECODE is needed."""
    scanner = FakeScanner({Opcode.START_DECODE: [[ACK, UPC_E]]})
    protocol = await connect(scanner)

    decoded = await protocol.scan(timeout=1)

    assert (decoded.symbology, decoded.data) == (ssi.Symbology.UPC_E, "04252614")
    assert scanner.opcodes() == [Opcode.START_DECODE, Opcode.CMD_ACK]


async def test_scan_catches_decode_data_arriving_before_the_ack(connect) -> None:
    """The subscription is open before START_DECODE goes out, so an early bar code is not lost."""
    protocol = await connect(FakeScanner({Opcode.START_DECODE: [[UPC_E, ACK]]}))

    assert (await protocol.scan(timeout=1)).data == "04252614"


async def test_scan_skips_events(connect) -> None:
    """An event during the session, e.g. a decode event, is not mistaken for the bar code."""
    protocol = await connect(FakeScanner({Opcode.START_DECODE: [[ACK, decoder(Opcode.EVENT, b"\x01"), UPC_E]]}))

    assert (await protocol.scan(timeout=1)).data == "04252614"


async def test_scan_without_a_bar_code_stops_the_session_and_reports_it(connect) -> None:
    """When the timeout passes, the session is stopped explicitly and NoBarcodeDecodedError is raised."""
    scanner = FakeScanner({Opcode.START_DECODE: [ACK], Opcode.STOP_DECODE: [ACK]})
    protocol = await connect(scanner)

    with pytest.raises(NoBarcodeDecodedError, match="0.2 s"):
        await protocol.scan(timeout=0.2)
    assert scanner.opcodes() == [Opcode.START_DECODE, Opcode.STOP_DECODE]


async def test_a_cancelled_scan_stops_the_session(connect) -> None:
    """Cancelling the scan, e.g. a client cancelling the SiLA command, does not leave the scanner scanning."""
    scanner = FakeScanner({Opcode.START_DECODE: [ACK], Opcode.STOP_DECODE: [ACK]})
    protocol = await connect(scanner)

    task = asyncio.create_task(protocol.scan(timeout=5))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert scanner.opcodes() == [Opcode.START_DECODE, Opcode.STOP_DECODE]


async def test_a_failed_stop_does_not_hide_the_missing_bar_code(connect) -> None:
    """If STOP_DECODE goes unanswered after a timeout, the caller still learns that nothing was decoded."""
    protocol = await connect(FakeScanner({Opcode.START_DECODE: [ACK]}))

    with pytest.raises(NoBarcodeDecodedError):
        await protocol.scan(timeout=0.1)


async def test_scan_is_refused_while_scanning_is_disabled(connect) -> None:
    """The precondition is checked before anything is sent to the scanner."""
    scanner = FakeScanner({Opcode.SCAN_DISABLE: [ACK]})
    protocol = await connect(scanner)
    await protocol.scan_disable()
    scanner.received.clear()

    with pytest.raises(ScanningDisabledError):
        await protocol.scan(timeout=1)
    assert scanner.received == []


async def test_a_second_scan_is_refused_while_one_is_in_progress(connect) -> None:
    """Concurrent scans would share one decode session, so the second raises ScannerBusyError."""
    scanner = FakeScanner({Opcode.START_DECODE: [ACK], Opcode.STOP_DECODE: [ACK]})
    protocol = await connect(scanner)

    first = asyncio.create_task(protocol.scan(timeout=0.3))
    await asyncio.sleep(0.05)
    with pytest.raises(ScannerBusyError):
        await protocol.scan(timeout=1)
    with pytest.raises(NoBarcodeDecodedError):
        await first

    assert scanner.opcodes().count(Opcode.START_DECODE) == 1


async def test_a_refused_session_start_is_reported(connect) -> None:
    """A DENIED START_DECODE, e.g. in a trigger mode that does not allow it, surfaces as CommandRejectedError."""
    protocol = await connect(FakeScanner({Opcode.START_DECODE: [nak(NakCause.DENIED)]}))

    with pytest.raises(CommandRejectedError, match="START_DECODE.*DENIED"):
        await protocol.scan(timeout=1)


async def test_stop_scan_ends_a_running_scan_and_stops_the_session(connect) -> None:
    """Stop Scan returns once the scan has sent STOP_DECODE; the scan ends with ScanStoppedError, not a timeout."""
    scanner = FakeScanner({Opcode.START_DECODE: [ACK], Opcode.STOP_DECODE: [ACK]})
    protocol = await connect(scanner)
    scan = asyncio.create_task(protocol.scan(timeout=5))
    await asyncio.sleep(0.05)

    await asyncio.wait_for(protocol.stop_scan(), timeout=1)

    assert scan.done()
    with pytest.raises(ScanStoppedError):
        await scan
    assert scanner.opcodes() == [Opcode.START_DECODE, Opcode.STOP_DECODE]


async def test_stop_scan_without_a_running_scan_does_nothing(connect) -> None:
    """Stopping is idempotent: nothing is sent, and a later scan is not stopped by the earlier request."""
    scanner = FakeScanner({Opcode.START_DECODE: [[ACK, UPC_E]]})
    protocol = await connect(scanner)

    await protocol.stop_scan()

    assert scanner.received == []
    assert (await protocol.scan(timeout=1)).data == "04252614"


async def test_decodes_yields_trigger_scans_and_skips_events(connect) -> None:
    """Every decode data message becomes a parsed bar code, whatever started the decode."""
    protocol = await connect(FakeScanner())
    iterator = protocol.decodes()
    first = asyncio.ensure_future(anext(iterator))
    await settle()

    protocol.data_received(decoder(Opcode.EVENT, b"\x03"))
    protocol.data_received(UPC_E)
    protocol.data_received(decoder(Opcode.DECODE_DATA, b"\x031234567890"))

    assert (await first).data == "04252614"
    assert (await anext(iterator)).symbology is ssi.Symbology.CODE_128
    await iterator.aclose()


# - Unsolicited packets ---------------------------------------------------------------------------------------------


async def test_decode_data_is_acknowledged_and_delivered(connect) -> None:
    """A scan result is ACKed and handed to every listener."""
    scanner = FakeScanner()
    protocol = await connect(scanner)
    frame = decoder(Opcode.DECODE_DATA, b"\x0b3456789012340")

    with protocol.listen() as first, protocol.listen() as second:
        protocol.data_received(frame)
        await settle()

        assert first.get_nowait() == second.get_nowait() == Packet.decode(frame)
    assert scanner.opcodes() == [Opcode.CMD_ACK]


async def test_multipacket_decode_data_is_joined_and_every_packet_acknowledged(connect) -> None:
    """Each packet is ACKed; the repeated bar code type is dropped when the message is joined."""
    scanner = FakeScanner()
    protocol = await connect(scanner)

    with protocol.listen() as messages:
        protocol.data_received(decoder(Opcode.DECODE_DATA, b"\x1bABC", status=Status.CONTINUATION))
        protocol.data_received(decoder(Opcode.DECODE_DATA, b"\x1bDEF"))
        await settle()

        assert ssi.parse_decode_data(messages.get_nowait().data) == ssi.Barcode(ssi.Symbology.DATA_MATRIX, "ABCDEF")
        assert messages.empty()
    assert scanner.opcodes() == [Opcode.CMD_ACK, Opcode.CMD_ACK]


async def test_a_resent_scan_is_acknowledged_again_but_delivered_once(connect) -> None:
    """A retransmission of the previous packet, e.g. after a late ACK, must not produce a second scan."""
    scanner = FakeScanner()
    protocol = await connect(scanner)
    data = b"\x0304252614"

    with protocol.listen() as messages:
        protocol.data_received(decoder(Opcode.DECODE_DATA, data))
        protocol.data_received(decoder(Opcode.DECODE_DATA, data, status=Status.RETRANSMIT))
        protocol.data_received(decoder(Opcode.DECODE_DATA, data))  # the same bar code scanned again
        await settle()

        assert messages.qsize() == 2
    assert scanner.opcodes() == [Opcode.CMD_ACK] * 3


async def test_a_scan_during_a_request_does_not_answer_the_request(connect) -> None:
    """Decode data arriving before a reply goes to listeners; the request still gets its own reply."""
    scan = decoder(Opcode.DECODE_DATA, b"\x031234567890")
    protocol = await connect(FakeScanner({Opcode.REQUEST_REVISION: [[scan, REVISION_REPLY]]}))

    with protocol.listen() as messages:
        assert await protocol.get_revision() == "PAACYS00-010-R01"
        assert messages.get_nowait() == Packet.decode(scan)


async def test_events_are_delivered(connect) -> None:
    """EVENT packets, e.g. the boot-up event, reach listeners like decode data."""
    protocol = await connect(FakeScanner())

    with protocol.listen() as messages:
        protocol.data_received(decoder(Opcode.EVENT, b"\x03"))
        await settle()

        assert ssi.parse_event(messages.get_nowait().data) is ssi.EventCode.BOOT_UP


async def test_a_corrupt_packet_is_naked_and_the_stream_recovers(connect) -> None:
    """A checksum failure asks the scanner to resend; the next good packet is processed normally."""
    scanner = FakeScanner()
    protocol = await connect(scanner)
    good = decoder(Opcode.DECODE_DATA, b"\x031234567890")

    with protocol.listen() as messages:
        protocol.data_received(good[:-1] + bytes([good[-1] ^ 0xFF]))
        protocol.data_received(good)
        await settle()

        assert messages.get_nowait() == Packet.decode(good)
    assert scanner.received[0] == Packet(Opcode.CMD_NAK, bytes([NakCause.RESEND]))


async def test_an_incomplete_packet_is_dropped_after_the_character_timeout(connect) -> None:
    """Bytes that never complete a packet, e.g. raw ASCII, do not block the packets after them."""
    protocol = await connect(FakeScanner())
    good = decoder(Opcode.DECODE_DATA, b"\x031234567890")

    with protocol.listen() as messages:
        protocol.data_received(b"\x7a12")  # a length byte announcing 124 bytes that never arrive
        await asyncio.sleep(0.1)
        protocol.data_received(good)
        await settle()

        assert messages.get_nowait() == Packet.decode(good)


async def test_listeners_stop_receiving_once_their_context_closes(connect) -> None:
    """A closed `listen` context gets nothing more, so abandoned subscribers do not accumulate scans."""
    protocol = await connect(FakeScanner())

    with protocol.listen() as messages:
        pass
    protocol.data_received(decoder(Opcode.DECODE_DATA, b"\x031234567890"))
    await settle()

    assert messages.empty()


async def test_aclose_closes_even_if_queued_writes_never_drain(connect, monkeypatch: pytest.MonkeyPatch) -> None:
    """Waiting for the last ACK to go out is bounded, so teardown never hangs on a stuck port."""
    monkeypatch.setattr(protocol_module, "FLUSH_TIMEOUT", 0.05)
    protocol = await connect(FakeScanner())
    transport = protocol.transport
    monkeypatch.setattr(type(transport), "writes_pending", property(lambda _: True))

    await asyncio.wait_for(protocol.aclose(), timeout=1)

    assert transport.is_closing()


# - Cradle attributes -----------------------------------------------------------------------------------------------

PACKET_SIZE = decoder(Opcode.SSI_MGMT_COMMAND, bytes.fromhex("00 08 20 00 00 F0 00 F0"))


def attribute_reply(hex_string: str) -> bytes:
    return decoder(Opcode.SSI_MGMT_COMMAND, bytes.fromhex(hex_string))


async def test_cradle_information_is_read_after_one_packet_size_request(connect) -> None:
    """The PRG's required Get Packet Size goes out once per connection, before the first attribute read."""
    scanner = FakeScanner(
        {
            Opcode.SSI_MGMT_COMMAND: [
                PACKET_SIZE,
                attribute_reply(
                    "00 21 02 00 02 15 53 01 00 13 00 00 43 52 38 31 37 38 2D 53 43 31 30 30 46 34 57 57 20 20 00 FF FF"
                ),
                attribute_reply("00 16 02 00 02 17 53 01 00 08 00 00 31 30 44 45 43 32 35 00 FF FF"),
            ]
        }
    )
    protocol = await connect(scanner)

    assert await protocol.get_cradle_model() == "CR8178-SC100F4WW"
    assert await protocol.get_cradle_manufacture_date() == datetime.date(2025, 12, 10)
    assert [packet.data[2] for packet in scanner.received] == [0x20, 0x02, 0x02]


async def test_an_unsupported_cradle_attribute_is_unexpected(connect) -> None:
    """A cradle that leaves the serial number out of its reply is reported rather than returning nothing."""
    protocol = await connect(FakeScanner({Opcode.SSI_MGMT_COMMAND: [PACKET_SIZE, attribute_reply("00 06 02 00 FF FF")]}))

    with pytest.raises(UnexpectedResponseError, match="SERIAL_NUMBER"):
        await protocol.get_cradle_serial_number()


async def test_an_unprogrammed_manufacture_date_is_unexpected(connect) -> None:
    """The DDMMMYY placeholder some date attributes carry (e.g. attribute 614) is not a date."""
    placeholder = attribute_reply("00 16 02 00 02 17 53 01 00 08 00 00 44 44 4D 4D 4D 59 59 00 FF FF")
    protocol = await connect(FakeScanner({Opcode.SSI_MGMT_COMMAND: [PACKET_SIZE, placeholder]}))

    with pytest.raises(UnexpectedResponseError, match="DDMMMYY"):
        await protocol.get_cradle_manufacture_date()
