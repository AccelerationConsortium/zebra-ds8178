"""
Quick SSI connectivity test for the Zebra DS8178 (via CR8178 cradle, USB CDC).

Checks whether the scanner speaks SSI, makes it beep, then listens for scans.
Requires: pip install pyserial

Packet format (per Zebra SSI protocol - verify against the
Simple Serial Interface Programmer's Guide, 72-40451-xx):
  [Length][Opcode][Source][Status][Data...][Checksum hi][Checksum lo]
  Length   = bytes before the checksum (including the length byte itself)
  Source   = 0x04 host -> scanner, 0x00 scanner -> host
  Checksum = 16-bit two's complement of the sum of all preceding bytes
"""
import sys
import time

import serial
from serial.tools import list_ports

ZEBRA_VID = 0x05E0
SRC_HOST = 0x04

# Opcodes
CMD_ACK = 0xD0
CMD_NAK = 0xD1
REQUEST_REVISION = 0xA3
REPLY_REVISION = 0xA4
BEEP = 0xE6
DECODE_DATA = 0xF3
START_DECODE = 0xE4
STOP_DECODE = 0xE5

NAMES = {CMD_ACK: "CMD_ACK", CMD_NAK: "CMD_NAK", REPLY_REVISION: "REPLY_REVISION",
         DECODE_DATA: "DECODE_DATA"}


def find_port() -> str:
    for p in list_ports.comports():
        if p.vid == ZEBRA_VID:
            return p.device
    sys.exit("No Zebra scanner found (VID 0x05E0). Is the cradle plugged in?")


def build_packet(opcode: int, data: bytes = b"", status: int = 0x00) -> bytes:
    body = bytes([4 + len(data), opcode, SRC_HOST, status]) + data
    checksum = (-sum(body)) & 0xFFFF
    return body + checksum.to_bytes(2, "big")


def checksum_ok(pkt: bytes) -> bool:
    return len(pkt) >= 6 and (sum(pkt[:-2]) + int.from_bytes(pkt[-2:], "big")) & 0xFFFF == 0


def read_packet(ser: serial.Serial, timeout: float = 2.0) -> bytes | None:
    ser.timeout = timeout
    first = ser.read(1)
    if not first:
        return None
    rest = ser.read(first[0] - 1 + 2)  # remaining body + 2 checksum bytes
    return first + rest


def send(ser: serial.Serial, pkt: bytes) -> None:
    print(f"  -> {pkt.hex(' ').upper()}")
    ser.write(pkt)


def describe(pkt: bytes) -> str:
    if not checksum_ok(pkt):
        return f"NOT a valid SSI packet (raw: {pkt!r})"
    op = pkt[1]
    return f"{NAMES.get(op, hex(op))}  [{pkt.hex(' ').upper()}]"


def main() -> None:
    port = find_port()
    print(f"Using {port}")
    with serial.Serial(port, 9600, timeout=2) as ser:  # baud is ignored over USB CDC
        ser.reset_input_buffer()
 
        # 1) Ask for firmware revision - the definitive "is this SSI?" check
        print("\n[1] REQUEST_REVISION")
        send(ser, build_packet(REQUEST_REVISION))
        reply = read_packet(ser)
        if reply is None:
            print("  <- no reply.\n"
                  "  Scanner is probably NOT in 'SSI over USB CDC' mode (or not linked to the cradle).\n"
                  "  Scan 'SSI over USB CDC' (manual p. 8-6 / PDF p. 211) and run again.")
            return
        print(f"  <- {describe(reply)}")
        if checksum_ok(reply) and reply[1] == REPLY_REVISION:
            print(f"  Firmware: {reply[4:-2].decode(errors='replace')}")
            send(ser, build_packet(CMD_ACK))
            print("  SSI confirmed.")
 
        # 2) Make it beep (beep code 0x00 = one short high beep)
        print("\n[2] BEEP")
        send(ser, build_packet(BEEP, bytes([0x00])))
        reply = read_packet(ser)
        print(f"  <- {describe(reply) if reply else 'no reply'}")
 
        # 3) Listen for manual scans
        print("\n[3] Pull the trigger and scan some barcodes (15 s)...")
        end = time.time() + 15
        while time.time() < end:
            pkt = read_packet(ser, timeout=1.0)
            if not pkt:
                continue
            if checksum_ok(pkt) and pkt[1] == DECODE_DATA:
                payload = pkt[4:-2]
                print(f"  <- DECODE_DATA  type=0x{payload[0]:02X}  data={payload[1:].decode(errors='replace')!r}")
                send(ser, build_packet(CMD_ACK))
            else:
                print(f"  <- {describe(pkt)}")
                print("     (raw text here means 'Decode Data Packet Format' is set to unpacketed)")
 
        # 4) Host-triggered scan - this is what the connector will use
        input("\n[4] Hold a barcode in front of the scanner, then press Enter...")
        send(ser, build_packet(START_DECODE))
        end = time.time() + 5
        got_data = False
        while time.time() < end and not got_data:
            pkt = read_packet(ser, timeout=1.0)
            if not pkt:
                continue
            if checksum_ok(pkt) and pkt[1] == DECODE_DATA:
                payload = pkt[4:-2]
                print(f"  <- DECODE_DATA  type=0x{payload[0]:02X}  data={payload[1:].decode(errors='replace')!r}")
                send(ser, build_packet(CMD_ACK))
                got_data = True
            else:
                print(f"  <- {describe(pkt)}")
        if not got_data:
            print("  No barcode decoded within 5 s - stopping decode session.")
            send(ser, build_packet(STOP_DECODE))
            reply = read_packet(ser)
            print(f"  <- {describe(reply) if reply else 'no reply'}")
 
 
if __name__ == "__main__":
    main()
 