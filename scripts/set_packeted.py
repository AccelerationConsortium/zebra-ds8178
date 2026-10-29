"""
Switch the DS8178 to "Send Packeted Decode Data" via SSI PARAM_SEND.

Parameter: Decode Data Packet Format, # 238 (SSI 0xEE), value 1 = packeted
(manual p. 9-16). Saved permanently, so it survives power cycles.
Run from the same folder as ssi_test.py.
"""
import serial

from scripts.ssi_test import CMD_ACK, build_packet, describe, find_port, read_packet, send

PARAM_SEND = 0xC6
STATUS_PERMANENT = 0x08   # status bit 3: store permanently (vs. until power-down)
BEEP_NONE = 0xFF          # no confirmation beep
DECODE_DATA_PACKET_FORMAT = 0xEE
PACKETED = 0x01

port = find_port()
print(f"Using {port}")
with serial.Serial(port, 9600, timeout=2) as ser:
    ser.reset_input_buffer()
    data = bytes([BEEP_NONE, DECODE_DATA_PACKET_FORMAT, PACKETED])
    send(ser, build_packet(PARAM_SEND, data, status=STATUS_PERMANENT))
    reply = read_packet(ser)
    print(f"  <- {describe(reply) if reply else 'no reply'}")
    if reply and reply[1] == CMD_ACK:
        print("Done - decode data will now arrive as SSI packets.")