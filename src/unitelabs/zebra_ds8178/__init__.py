import collections.abc
from importlib.metadata import version

from unitelabs.cdk import Connector
from unitelabs.cdk.features.core.lock_controller import LockController

from .config import ZebraDs8178Config

from .io import ZebraDs8178Protocol

__version__ = version("unitelabs-zebra-ds8178")


async def create_app(config: ZebraDs8178Config) -> collections.abc.AsyncGenerator[Connector, None]:
    """Create the connector application."""

    app = Connector(config)
    app.register(LockController())

    protocol = ZebraDs8178Protocol()

    try:
        await protocol.open()

        yield app
    finally:
        protocol.close()
