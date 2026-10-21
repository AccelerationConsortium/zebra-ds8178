import dataclasses
from importlib.metadata import version

from unitelabs.cdk import ConnectorBaseConfig, SiLAServerConfig

__version__ = version("unitelabs-zebra-ds8178")


@dataclasses.dataclass
class ZebraDs8178Config(ConnectorBaseConfig):
    """Configuration for the Zebra DS8178."""

    sila_server: SiLAServerConfig = dataclasses.field(
        default_factory=lambda: SiLAServerConfig(
            name="Zebra DS8178",
            type="Example",
            description=(
                """
                A SiLA2 connector for the Zebra DS8178 Digital Scanner
                """
            ),
            version=str(__version__),
            vendor_url="https://unitelabs.io/",
        )
    )
