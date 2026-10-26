import collections.abc
import contextlib

import pytest
from sila.client import Client, ClientConfig

from unitelabs.zebra_ds8178 import create_app
from unitelabs.zebra_ds8178.config import ZebraDs8178Config

pytest_plugins = ["unitelabs.bus.testing.fixtures"]


@pytest.fixture
def serve():
    """Start the connector on a free local port with the given config changes, and connect a SiLA client to it."""

    @contextlib.asynccontextmanager
    async def _serve(**changes) -> collections.abc.AsyncGenerator[Client, None]:
        config = ZebraDs8178Config.validate(
            {"sila_server": {"hostname": "127.0.0.1", "port": 0, "tls": False}, "discovery": None, **changes}
        )
        config.cloud_servers.enabled = False
        app_lifecycle = create_app(config)
        app = await anext(app_lifecycle)
        await app.start()
        try:
            port = int(app.sila_server._address.rpartition(":")[2])
            async with Client(ClientConfig(hostname="127.0.0.1", port=port, tls=False, default_timeout=10_000)) as client:
                yield client
        finally:
            await app.stop()
            with contextlib.suppress(StopAsyncIteration):
                await app_lifecycle.aclose()

    return _serve


def pytest_addoption(parser: pytest.Parser) -> None:
    """Options for the hardware tests in tests/integration, which are skipped without a serial port."""
    parser.addoption("--serial-port", help="Serial port of a CR8178 cradle in SSI over USB CDC mode.")
    parser.addoption(
        "--with-barcode",
        action="store_true",
        help="Also run the tests that read a bar code: hold one in front of the scanner window when prompted.",
    )
