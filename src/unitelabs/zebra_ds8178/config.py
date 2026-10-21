import dataclasses
from importlib.metadata import version

from unitelabs.cdk import ConnectorBaseConfig, SiLAServerConfig

__version__ = version("unitelabs-zebra-ds8178")


@dataclasses.dataclass
class ZebraDs8178Config(ConnectorBaseConfig):
    """Configuration for the Zebra DS8178."""

    serial_port: str | None = None
    """
    Serial port the scanner's cradle appears as, e.g. /dev/ttyACM0 on Linux, /dev/cu.usbmodem1201 on macOS or COM3
    on Windows. Leave empty to find the cradle automatically by its Zebra USB ID. The scanner must be set to
    "SSI over USB CDC" mode by scanning that programming bar code from the Product Reference Guide.
    """

    simulation_mode: bool = True
    """Start with a simulated scanner instead of the physical one; switch at runtime with the Simulation Controller."""

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            name="Zebra DS8178",
            type="BarcodeReader",
            description="A SiLA 2 connector for the Zebra DS8178 cordless 1D/2D barcode scanner and its cradle.",
            version=str(__version__),
            vendor_url="https://unitelabs.io/",
        )
    )
