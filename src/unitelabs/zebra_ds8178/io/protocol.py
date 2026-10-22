import asyncio
import collections.abc
import contextlib
import dataclasses

import serial.tools.list_ports

from unitelabs.bus import CommandExecutionError, Protocol, TransportFactory, create_serial_connection

from ..constants import CHARACTER_TIMEOUT, OPEN_TIMEOUT, RETRIES, ZEBRA_USB_VENDOR_ID
from .errors import CommandRejectedError, ScannerNotRespondingError, SerialPortNotFoundError, UnexpectedResponseError
from .ssi import (
    NakCause,
    Opcode,
    Packet,
    SsiCommand,
    Status,
    encode_param_number,
    encode_param_send,
    frame_length,
    join_message,
    parse_nak_cause,
    parse_param_send,
    parse_revision,
)

DECODE_DATA_PACKET_FORMAT = 238
"""Parameter selecting raw (0) or packeted (1) decode data (Product Reference Guide p. 9-16)."""

PACKETED = 1

UNSOLICITED = (Opcode.DECODE_DATA, Opcode.EVENT)
"""Opcodes the scanner sends on its own rather than as a reply, each of which the host must ACK (SSI guide 1-2)."""


class ZebraDs8178Protocol(Protocol):
    """
    Simple Serial Interface to a DS8178 scanner through its cradle's USB CDC serial port.

    Requests are sent one at a time and answered with CMD_ACK, CMD_NAK or a reply packet. Decode data and events
    arrive unsolicited at any time; they are acknowledged here and handed to every `listen` subscriber.
    """

    def __init__(self, port: str | None = None, **kwargs):
        super().__init__(create_serial_connection, **kwargs)
        self._port = port
        self._buffer = b""
        self._stale_buffer: asyncio.TimerHandle | None = None
        self._unsolicited: list[Packet] = []
        self._previous_unsolicited: Packet | None = None
        self._listeners: set[asyncio.Queue[Packet]] = set()

    async def open_device(self) -> None:
        """
        Connect to the scanner on the configured serial port, or on the one Zebra cradle found by its USB ID.

        Raises:
          SerialPortNotFoundError: If no single Zebra cradle is found, or the serial port cannot be opened.
          ScannerNotRespondingError: If the port opens but the scanner does not answer in time.
        """
        port = self._port or self._find_cradle_port()
        try:
            await self._reopen(create_serial_connection, {"port": port})
        except TimeoutError as error:
            msg = (
                f"No scanner answered on {port} within {OPEN_TIMEOUT:g} s; check that it is paired to the cradle and "
                'set to "SSI over USB CDC" mode, then retry.'
            )
            raise ScannerNotRespondingError(msg) from error
        except OSError as error:  # after TimeoutError, which subclasses it
            msg = f"Cannot open serial port {port} ({error}); check the cable and that no other program uses it."
            raise SerialPortNotFoundError(msg) from error

    async def identity(self, **_kwargs) -> bool:
        """
        Confirm an SSI scanner answers and switch it to packeted decode data; called on every (re)connect.

        The packet format is checked and only written when it differs, as a permanent change: a temporary one is lost
        when the scanner powers down while the cradle stays connected, and each permanent write wears the scanner's
        non-volatile memory (Product Reference Guide p. 9-6).
        """
        try:
            await self.get_revision()
            parameters = await self.get_parameters(DECODE_DATA_PACKET_FORMAT)
            if parameters.get(DECODE_DATA_PACKET_FORMAT) != PACKETED:
                await self.set_parameters({DECODE_DATA_PACKET_FORMAT: PACKETED}, permanent=True)
        except (ScannerNotRespondingError, CommandRejectedError, UnexpectedResponseError):
            self.logger.warning("Scanner did not complete the connection checks.", exc_info=True)
            return False  # `Protocol.validate` keeps waiting, bounded by `_reopen`

        return True

    async def request(self, packet: Packet, reply: Opcode = Opcode.CMD_ACK) -> bytes:
        """
        Send `packet` and return the data of its reply, resending on timeout or checksum failure.

        Args:
          packet: The request to send.
          reply: The opcode of the expected reply; CMD_ACK for commands without a natural reply.

        Raises:
          ScannerNotRespondingError: If there is no open connection, or no reply after the initial send and
            `RETRIES` resends (Product Reference Guide p. 9-6).
          CommandRejectedError: If the scanner answers with a CMD_NAK other than a resend request.
          UnexpectedResponseError: If the scanner answers with another opcode than `reply`.
        """
        for attempt in range(RETRIES + 1):
            if attempt:
                packet = dataclasses.replace(packet, status=packet.status | Status.RETRANSMIT)
            try:
                response = await self.execute(SsiCommand(packet))
            except TimeoutError:
                continue
            except CommandExecutionError as error:
                msg = f"There is no open connection to the scanner ({error}); use Start Real Mode to reconnect."
                raise ScannerNotRespondingError(msg) from error

            if response.opcode == Opcode.CMD_NAK:
                cause = parse_nak_cause(response.data)
                if cause is NakCause.RESEND:
                    continue
                msg = f"The scanner refused {_name(packet.opcode)} ({cause.name})."
                raise CommandRejectedError(msg)
            if response.opcode != reply:
                msg = f"Expected {_name(reply)} in reply to {_name(packet.opcode)}, received {_name(response.opcode)}."
                raise UnexpectedResponseError(msg)

            return response.data

        msg = f"The scanner did not answer {_name(packet.opcode)} after {RETRIES + 1} attempts."
        raise ScannerNotRespondingError(msg)

    @contextlib.contextmanager
    def listen(self) -> collections.abc.Generator[asyncio.Queue[Packet], None, None]:
        """Receive every unsolicited message, e.g. decode data or an event, that arrives while the context is open."""
        queue: asyncio.Queue[Packet] = asyncio.Queue()
        self._listeners.add(queue)
        try:
            yield queue
        finally:
            self._listeners.discard(queue)

    async def get_revision(self) -> str:
        """
        Request the scanner's software revision, e.g. `PAACYS00-010-R01` (REQUEST_REVISION, SSI guide p. 2-60).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          UnexpectedResponseError: If the reply is not a revision string.
        """
        return parse_revision(await self.request(Packet(Opcode.REQUEST_REVISION), reply=Opcode.REPLY_REVISION))

    async def get_parameters(self, *numbers: int) -> dict[int, int | bytes]:
        """
        Read parameter values; parameters the scanner does not support are absent (PARAM_REQUEST, SSI guide p. 2-54).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          UnexpectedResponseError: If the reply cannot be parsed.
        """
        request = Packet(Opcode.PARAM_REQUEST, b"".join(encode_param_number(number) for number in numbers))
        return parse_param_send(await self.request(request, reply=Opcode.PARAM_SEND))

    async def set_parameters(self, values: dict[int, int], permanent: bool = False) -> None:
        """
        Set byte-valued parameters without a confirmation beep (PARAM_SEND, SSI guide p. 2-57).

        Args:
          values: Parameter numbers and their new values.
          permanent: Store in non-volatile memory instead of until the scanner powers down.

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses a parameter or value.
        """
        status = Status.PERMANENT if permanent else Status.NONE
        await self.request(Packet(Opcode.PARAM_SEND, encode_param_send(values), status=status))

    def data_received(self, data: bytes) -> None:
        """Cut the byte stream into packets; acknowledge unsolicited ones and pass replies to the pending command."""
        if self._stale_buffer:
            self._stale_buffer.cancel()
            self._stale_buffer = None
        self._buffer += data

        while self._buffer:
            size = frame_length(self._buffer[0])
            if len(self._buffer) < size:
                self._stale_buffer = asyncio.get_running_loop().call_later(CHARACTER_TIMEOUT, self._drop_stale_buffer)
                return

            frame, self._buffer = self._buffer[:size], self._buffer[size:]
            try:
                packet = Packet.decode(frame)
            except UnexpectedResponseError:
                # A corrupt length byte leaves no way to find the next packet, so the rest is dropped too.
                self.logger.warning("Dropping invalid data %s.", (frame + self._buffer).hex(" "))
                self._buffer = b""
                self._send(Packet(Opcode.CMD_NAK, bytes([NakCause.RESEND])))
                return

            if packet.opcode in UNSOLICITED:
                self._receive_unsolicited(packet)
            else:
                super().data_received(frame)

    def _receive_unsolicited(self, packet: Packet) -> None:
        """
        Acknowledge every packet and deliver each complete message once.

        Every packet is ACKed, not only the last one of a multipacket message: under Multipacket Option 1, the
        default, the scanner waits for an ACK per packet, and under Options 2 and 3 the extra ACKs are ignored as
        unsolicited (SSI guide pp. 1-7 and 1-10). A resend of the previous packet, caused by an ACK that did not
        arrive in time, is acknowledged again but not delivered twice.
        """
        self._send(Packet(Opcode.CMD_ACK))

        original = dataclasses.replace(packet, status=packet.status & ~Status.RETRANSMIT)
        if Status.RETRANSMIT in packet.status and original == self._previous_unsolicited:
            return
        self._previous_unsolicited = original

        self._unsolicited.append(packet)
        if packet.is_continued:
            return

        message, self._unsolicited = join_message(self._unsolicited), []
        for listener in self._listeners:
            listener.put_nowait(message)

    def _send(self, packet: Packet) -> None:
        if self.transport and not self.transport.is_closing():
            self.logger.info("> %s", packet.encode())
            self.transport.write(packet.encode())

    def _drop_stale_buffer(self) -> None:
        self.logger.warning("Dropping incomplete packet %s.", self._buffer.hex(" "))
        self._buffer, self._stale_buffer = b"", None

    async def _reopen(self, transport_factory: TransportFactory, transport_kwargs: dict) -> None:
        """Replace the current transport, bounded in time because a port that never opens does not raise."""
        self.close()
        self._buffer, self._unsolicited, self._previous_unsolicited = b"", [], None
        self._transport_factory = transport_factory
        self._transport_kwargs = transport_kwargs
        try:
            await asyncio.wait_for(self.open(validation_timeout=OPEN_TIMEOUT), timeout=OPEN_TIMEOUT)
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _find_cradle_port() -> str:
        """
        Find the serial port of the one connected Zebra cradle by its USB vendor ID.

        Raises:
          SerialPortNotFoundError: If no or several Zebra devices are connected.
        """
        ports = sorted(port.device for port in serial.tools.list_ports.comports() if port.vid == ZEBRA_USB_VENDOR_ID)
        if len(ports) != 1:
            found = ", ".join(ports) or "none"
            msg = f"Expected one Zebra cradle on a serial port, found {found}; set 'serial_port' in the configuration."
            raise SerialPortNotFoundError(msg)

        return ports[0]


def _name(opcode: int) -> str:
    try:
        return Opcode(opcode).name
    except ValueError:
        return f"opcode 0x{opcode:02X}"
