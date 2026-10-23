import collections.abc

import pytest

from unitelabs.cdk import Connector
from unitelabs.zebra_ds8178.config import ZebraDs8178Config
from unitelabs.zebra_ds8178.io import SimulatedScanner, ZebraDs8178Protocol


@pytest.fixture
async def simulated() -> collections.abc.AsyncGenerator[collections.abc.Callable, None]:
    """Open a protocol to a simulated scanner, closed again after the test."""
    opened: list[ZebraDs8178Protocol] = []

    async def _simulated(scanner: SimulatedScanner | None = None) -> ZebraDs8178Protocol:
        protocol = ZebraDs8178Protocol(simulated_scanner=scanner)
        await protocol.open_simulation()
        opened.append(protocol)
        return protocol

    yield _simulated
    for protocol in opened:
        await protocol.aclose()


@pytest.fixture
def registered() -> collections.abc.Callable:
    """Register a feature with a connector, so the CDK derives its SiLA interface from the code."""

    def _registered(feature_type: type, *args) -> object:
        feature = feature_type(ZebraDs8178Protocol(), *args)
        Connector(ZebraDs8178Config()).register(feature)
        return feature

    return _registered
