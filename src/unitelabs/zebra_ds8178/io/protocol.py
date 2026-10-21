from unitelabs.bus import Protocol, create_serial_connection


class ZebraDs8178Protocol(Protocol):
    """Underlying communication protocol for Zebra DS8178."""

    def __init__(self, **kwargs):
        super().__init__(create_serial_connection, **kwargs)
