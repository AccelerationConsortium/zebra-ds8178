import collections.abc

import pytest

from unitelabs.zebra_ds8178.io import ZebraDs8178Protocol


@pytest.fixture
async def scanner(request: pytest.FixtureRequest) -> collections.abc.AsyncGenerator[ZebraDs8178Protocol, None]:
    """A protocol connected to the real cradle given with `--serial-port`; the test is skipped without one."""
    port = request.config.getoption("--serial-port")
    if not port:
        pytest.skip("needs a real DS8178 on its cradle: run with --serial-port <port>")

    protocol = ZebraDs8178Protocol(port=port)
    await protocol.open_device()
    try:
        yield protocol
    finally:
        await protocol.aclose()


@pytest.fixture
def barcode(request: pytest.FixtureRequest) -> None:
    """Skip unless `--with-barcode` says someone holds a bar code in front of the scanner."""
    if not request.config.getoption("--with-barcode"):
        pytest.skip("needs a bar code in front of the scanner: run with --with-barcode")
