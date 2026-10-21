from unitelabs.bus import Protocol, create_usb_connection


class ZebraDs8178Protocol(Protocol):
    """Underlying communication protocol for Zebra DS8178."""

    def __init__(self, **kwargs):
        kwargs["vendor"] = 0xCAFE  # FIXME: set device vendor id
        kwargs["product"] = 0x0001  # FIXME: set device product id
        super().__init__(create_usb_connection, **kwargs)
