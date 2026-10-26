"""The connector app as `connector start` builds it."""

import collections.abc
import contextlib
import functools

import pytest
import typing_extensions as typing

from unitelabs.bus.testing import create_mock_connection
from unitelabs.cdk import Connector
from unitelabs.zebra_ds8178 import create_app
from unitelabs.zebra_ds8178.config import ZebraDs8178Config
from unitelabs.zebra_ds8178.features import BarcodeReaderController, SimulationController
from unitelabs.zebra_ds8178.io import ScannerNotRespondingError
from unitelabs.zebra_ds8178.io import protocol as protocol_module

F = typing.TypeVar("F")


@contextlib.asynccontextmanager
async def running(config: ZebraDs8178Config) -> collections.abc.AsyncGenerator[Connector, None]:
    """Start the connector app like `connector start` does, and tear it down afterwards."""
    app_lifecycle = create_app(config)
    app = await anext(app_lifecycle)
    try:
        yield app
    finally:
        await app_lifecycle.aclose()


def registered(app: Connector, feature_class: type[F]) -> F:
    """Return the registered feature of the given class."""
    return next(feature for feature in app.sila_server.features.values() if isinstance(feature, feature_class))


async def test_registers_every_feature() -> None:
    """The scanner features and the standard Simulation and Lock Controllers are all served."""
    async with running(ZebraDs8178Config()) as app:
        assert {
            "SimulationController",
            "LockController",
            "BarcodeReaderController",
            "BeeperController",
            "CradleInformationProvider",
        } <= {feature.identifier for feature in app.sila_server.features.values()}


async def test_starts_in_simulation_mode_by_default() -> None:
    """`connector dev` relies on this: a fresh config needs no hardware."""
    async with running(ZebraDs8178Config()) as app:
        assert await registered(app, SimulationController).simulation_mode() is True
        assert (await registered(app, BarcodeReaderController).scan(timeout=1)).data == "012345678905"


async def test_real_mode_without_an_answering_scanner_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """With simulation off, a port that never answers fails startup with a bounded wait, instead of hanging."""
    monkeypatch.setattr(protocol_module, "OPEN_TIMEOUT", 0.5)
    monkeypatch.setattr(protocol_module, "create_serial_connection", functools.partial(create_mock_connection))

    with pytest.raises(ScannerNotRespondingError, match="/dev/ttyACM0"):
        async with running(ZebraDs8178Config.validate({"simulation_mode": False, "serial_port": "/dev/ttyACM0"})):
            pass
