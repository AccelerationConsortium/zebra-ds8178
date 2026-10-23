import asyncio
import collections.abc
import contextlib
import dataclasses
import datetime

import serial.tools.list_ports

from unitelabs.bus import CommandExecutionError, Protocol, TransportFactory, create_serial_connection

from ..constants import CHARACTER_TIMEOUT, FLUSH_TIMEOUT, OPEN_TIMEOUT, RETRIES, ZEBRA_USB_VENDOR_ID
from .errors import (
    CommandRejectedError,
    NoBarcodeDecodedError,
    ScannerBusyError,
    ScannerNotRespondingError,
    ScanningDisabledError,
    SerialPortNotFoundError,
    UnexpectedResponseError,
)
from .rsm import Attribute, encode_attribute_get, encode_get_packet_size, parse_attribute_get
from .simulation import SimulatedScanner
from .ssi import (
    BeepCode,
    DecodeData,
    NakCause,
    Opcode,
    Packet,
    SsiCommand,
    Status,
    encode_param_number,
    encode_param_send,
    frame_length,
    join_message,
    parse_decode_data,
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

    def __init__(self, port: str | None = None, simulated_scanner: SimulatedScanner | None = None, **kwargs):
        super().__init__(create_serial_connection, **kwargs)
        self._port = port
        # Created once, so the simulated scanner keeps its settings across mode switches like one left in its cradle.
        self._simulated_scanner = simulated_scanner or SimulatedScanner()
        self._buffer = b""
        self._stale_buffer: asyncio.TimerHandle | None = None
        self._unsolicited: list[Packet] = []
        self._previous_unsolicited: Packet | None = None
        self._listeners: set[asyncio.Queue[Packet]] = set()
        self._scanning_enabled = True
        self._scan_lock = asyncio.Lock()
        self._rsm_ready = False

    async def open_device(self) -> None:
        """
        Connect to the scanner on the configured serial port, or on the one Zebra cradle found by its USB ID.

        Raises:
          ScannerBusyError: If a scan is in progress, which switching the connection would cut off.
          SerialPortNotFoundError: If no single Zebra cradle is found, or the serial port cannot be opened.
          ScannerNotRespondingError: If the port opens but the scanner does not answer in time.
        """
        self._check_not_scanning()
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

    async def open_simulation(self) -> None:
        """
        Connect to the simulated scanner, which keeps its settings and scanning state across mode switches.

        Raises:
          ScannerBusyError: If a scan is in progress, which switching the connection would cut off.
        """
        await self._reopen(self._simulated_scanner.connection_factory(), {})

    async def aclose(self) -> None:
        """
        Close the connection once queued writes have gone out, waiting at most `FLUSH_TIMEOUT`.

        The serial transport closes its port at once, failing any write still buffered; an ACK lost that way makes
        the scanner resend the scan and then sound its transmit error beeps (SSI guide p. 2-26).
        """
        # The transport signals no event once its buffer drains, so it is polled.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + FLUSH_TIMEOUT
        while getattr(self.transport, "writes_pending", False) and loop.time() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.005)
        self.close()

    async def identity(self, **_kwargs) -> bool:
        """
        Confirm an SSI scanner answers, then set packeted decode data and enable scanning; runs on every (re)connect.

        The packet format is checked and only written when it differs, as a permanent change: a temporary one is lost
        when the scanner powers down while the cradle stays connected, and each permanent write wears the scanner's
        non-volatile memory (Product Reference Guide p. 9-6). Scanning is enabled so that `scanning_enabled` matches
        the scanner even if a previous connection left it disabled.
        """
        try:
            await self.get_revision()
            parameters = await self.get_parameters(DECODE_DATA_PACKET_FORMAT)
            if parameters.get(DECODE_DATA_PACKET_FORMAT) != PACKETED:
                await self.set_parameters({DECODE_DATA_PACKET_FORMAT: PACKETED}, permanent=True)
            await self.scan_enable()
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

    @property
    def scanning_enabled(self) -> bool:
        """Whether the scanner accepts trigger pulls and scan requests, as last set by `scan_enable`/`scan_disable`."""
        return self._scanning_enabled

    async def scan_enable(self) -> None:
        """
        Permit scanning by trigger and by request (SCAN_ENABLE, SSI guide p. 2-62).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses the request.
        """
        await self.request(Packet(Opcode.SCAN_ENABLE))
        self._scanning_enabled = True

    async def scan_disable(self) -> None:
        """
        Make the scanner ignore the trigger and scan requests (SCAN_DISABLE, SSI guide p. 2-61).

        Scanning stays disabled until `scan_enable`, a reconnect, or the scanner powering up again.

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses the request.
        """
        await self.request(Packet(Opcode.SCAN_DISABLE))
        self._scanning_enabled = False

    async def beep(self, code: BeepCode) -> None:
        """
        Sound a beep sequence (BEEP, SSI guide p. 2-10).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses the beep code.
        """
        await self.request(Packet(Opcode.BEEP, bytes([code])))

    async def scan(self, timeout: float) -> DecodeData:
        """
        Start a decode session, as if the trigger were pulled, and return the first bar code decoded.

        The subscription opens before START_DECODE is sent, so decode data arriving ahead of the ACK is not missed;
        a bar code read by a trigger pull during the session counts as well. The SSI guide requires the Host trigger
        mode for START_DECODE (p. 2-65), which the DS8178 does not list; on hardware it is accepted in the default
        Level mode, and refused as DENIED while scanning is disabled. The session is stopped when `timeout` passes or
        the scan is cancelled. The scanner also ends it after its own Decode Session Timeout, so `timeout` must not
        exceed it (see `MAX_SCAN_TIMEOUT`).

        Args:
          timeout: How long to wait for a bar code, in seconds.

        Raises:
          ScanningDisabledError: If scanning is disabled.
          ScannerBusyError: If another scan is in progress.
          NoBarcodeDecodedError: If no bar code is decoded within `timeout`.
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses to start the session.
        """
        if not self._scanning_enabled:
            msg = "Scanning is disabled; enable scanning, then retry."
            raise ScanningDisabledError(msg)
        if self._scan_lock.locked():
            msg = "A scan is already in progress; wait for it to finish, then retry."
            raise ScannerBusyError(msg)

        async with self._scan_lock:
            with self.listen() as messages:
                await self.start_decode()
                try:
                    return await asyncio.wait_for(self._next_decode(messages), timeout=timeout)
                except TimeoutError:
                    await self._stop_decode_quietly()
                    msg = f"No bar code was decoded within {timeout:g} s."
                    raise NoBarcodeDecodedError(msg) from None
                except BaseException:
                    await self._stop_decode_quietly()
                    raise

    async def decodes(self) -> collections.abc.AsyncIterator[DecodeData]:
        """Yield every bar code the scanner decodes from now on, by trigger pull, presentation or `scan`."""
        with self.listen() as messages:
            while True:
                yield await self._next_decode(messages)

    async def start_decode(self) -> None:
        """
        Start a decode session (START_DECODE, called START_SESSION in the SSI guide, p. 2-65).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses to start the session.
        """
        await self.request(Packet(Opcode.START_DECODE))

    async def stop_decode(self) -> None:
        """
        Abort the decode session (STOP_DECODE, called STOP_SESSION in the SSI guide, p. 2-66).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner refuses the request.
        """
        await self.request(Packet(Opcode.STOP_DECODE))

    async def aim_on(self) -> None:
        """
        Project the aiming pattern until `aim_off` (AIM_ON, SSI guide p. 2-8).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner has no aiming pattern it can switch on.
        """
        await self.request(Packet(Opcode.AIM_ON))

    async def aim_off(self) -> None:
        """
        Switch the aiming pattern off (AIM_OFF, SSI guide p. 2-7).

        Raises:
          ScannerNotRespondingError: If the scanner does not answer.
          CommandRejectedError: If the scanner has no aiming pattern it can switch off.
        """
        await self.request(Packet(Opcode.AIM_OFF))

    async def get_cradle_model(self) -> str:
        """
        Read the cradle's model number, e.g. `CR8178-SC100F4WW` (attribute 533).

        Raises:
          ScannerNotRespondingError: If the cradle does not answer.
          UnexpectedResponseError: If the cradle does not report its model number.
        """
        return await self._get_string_attribute(Attribute.MODEL_NUMBER)

    async def get_cradle_serial_number(self) -> str:
        """
        Read the cradle's serial number, as printed on its label (attribute 534).

        Raises:
          ScannerNotRespondingError: If the cradle does not answer.
          UnexpectedResponseError: If the cradle does not report its serial number.
        """
        return await self._get_string_attribute(Attribute.SERIAL_NUMBER)

    async def get_cradle_manufacture_date(self) -> datetime.date:
        """
        Read the date the cradle was made (attribute 535, reported as `DDMMMYY`, e.g. `10DEC25`).

        Raises:
          ScannerNotRespondingError: If the cradle does not answer.
          UnexpectedResponseError: If the cradle does not report a date in that format.
        """
        value = await self._get_string_attribute(Attribute.DATE_OF_MANUFACTURE)
        try:
            return datetime.datetime.strptime(value, "%d%b%y").date()
        except ValueError as error:
            msg = f"Expected a manufacture date like 10DEC25, received {value!r}."
            raise UnexpectedResponseError(msg) from error

    async def get_attributes(self, *attributes: int) -> dict[int, int | str | bytes]:
        """
        Read RSM attributes; over USB CDC these are the cradle's, and unsupported ones are absent (PRG p. 9-8).

        The Get Packet Size command the PRG requires before any RSM command is sent once per connection.

        Raises:
          ScannerNotRespondingError: If the cradle does not answer.
          CommandRejectedError: If the cradle does not support RSM.
          UnexpectedResponseError: If the reply cannot be parsed.
        """
        if not self._rsm_ready:
            await self.request(Packet(Opcode.SSI_MGMT_COMMAND, encode_get_packet_size()), reply=Opcode.SSI_MGMT_COMMAND)
            self._rsm_ready = True
        request = Packet(Opcode.SSI_MGMT_COMMAND, encode_attribute_get(*attributes))
        return parse_attribute_get(await self.request(request, reply=Opcode.SSI_MGMT_COMMAND))

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

    async def _get_string_attribute(self, attribute: Attribute) -> str:
        value = (await self.get_attributes(attribute)).get(attribute)
        if not isinstance(value, str):
            msg = f"The cradle did not report attribute {attribute} ({attribute.name}) as text, received {value!r}."
            raise UnexpectedResponseError(msg)

        return value

    @staticmethod
    async def _next_decode(messages: asyncio.Queue[Packet]) -> DecodeData:
        """Wait for the next decode data among the unsolicited messages, skipping events."""
        while (message := await messages.get()).opcode != Opcode.DECODE_DATA:
            pass
        return parse_decode_data(message.data)

    async def _stop_decode_quietly(self) -> None:
        """Stop a session that is ending anyway; a failure here must not hide the error that ended it."""
        try:
            await self.stop_decode()
        except (ScannerNotRespondingError, CommandRejectedError, UnexpectedResponseError):
            self.logger.warning("Could not stop the decode session.", exc_info=True)

    def _send(self, packet: Packet) -> None:
        if self.transport and not self.transport.is_closing():
            self.logger.info("> %s", packet.encode())
            self.transport.write(packet.encode())

    def _drop_stale_buffer(self) -> None:
        self.logger.warning("Dropping incomplete packet %s.", self._buffer.hex(" "))
        self._buffer, self._stale_buffer = b"", None

    async def _reopen(self, transport_factory: TransportFactory, transport_kwargs: dict) -> None:
        """
        Replace the current transport, bounded in time because a port that never opens does not raise.

        A scan in progress is checked before anything is closed, so a refused switch leaves the connection as it was.

        Raises:
          ScannerBusyError: If a scan is in progress.
        """
        self._check_not_scanning()
        await self.aclose()
        self._buffer, self._unsolicited, self._previous_unsolicited = b"", [], None
        self._rsm_ready = False
        self._transport_factory = transport_factory
        self._transport_kwargs = transport_kwargs
        try:
            await asyncio.wait_for(self.open(validation_timeout=OPEN_TIMEOUT), timeout=OPEN_TIMEOUT)
        except BaseException:
            self.close()
            raise

    def _check_not_scanning(self) -> None:
        """Refuse to replace the connection while a scan runs on it, before anything is looked up or closed."""
        if self._scan_lock.locked():
            msg = "A scan is in progress; wait for it to finish, then switch mode."
            raise ScannerBusyError(msg)

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
