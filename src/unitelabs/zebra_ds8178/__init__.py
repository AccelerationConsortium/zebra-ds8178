import collections.abc
from importlib.metadata import version

from unitelabs.cdk import Connector
from unitelabs.cdk.features.core.lock_controller import LockController

from .config import ZebraDs8178Config
from .features import BarcodeReaderController, BeeperController, CradleInformationProvider, SimulationController
from .io import ZebraDs8178Protocol

__version__ = version("unitelabs-zebra-ds8178")


async def create_app(config: ZebraDs8178Config) -> collections.abc.AsyncGenerator[Connector, None]:
    """Create the connector application."""

    app = Connector(config)
    protocol = ZebraDs8178Protocol(port=config.serial_port)

    app.register(SimulationController(protocol, simulation_mode=config.simulation_mode))
    if config.simulation_mode:
        await protocol.open_simulation()
    else:
        await protocol.open_device()

    app.register(BarcodeReaderController(protocol))
    app.register(BeeperController(protocol))
    app.register(CradleInformationProvider(protocol))
    app.register(LockController())
    try:
        yield app
    finally:
        await protocol.aclose()
