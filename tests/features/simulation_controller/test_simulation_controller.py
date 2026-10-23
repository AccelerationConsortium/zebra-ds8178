"""Switching between the simulated and the real scanner, and staying usable when the real one is unreachable."""

import asyncio
import functools

import pytest

from unitelabs.bus.testing import create_mock_connection
from unitelabs.cdk import Connector
from unitelabs.cdk.features.core.simulation_controller import StartRealModeFailed, StartSimulationModeFailed
from unitelabs.zebra_ds8178.config import ZebraDs8178Config
from unitelabs.zebra_ds8178.features import SimulationController
from unitelabs.zebra_ds8178.io import NoBarcodeDecodedError, SimulatedScanner, ZebraDs8178Protocol
from unitelabs.zebra_ds8178.io import protocol as protocol_module


@pytest.fixture(autouse=True)
def short_open_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protocol_module, "OPEN_TIMEOUT", 0.5)


async def simulated_controller(scanner: SimulatedScanner | None = None, port: str | None = None):
    protocol = ZebraDs8178Protocol(port=port, simulated_scanner=scanner)
    await protocol.open_simulation()
    return protocol, SimulationController(protocol, simulation_mode=True)


async def test_start_real_mode_without_a_cradle_stays_in_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no Zebra port found, the switch fails and the simulated scanner keeps answering."""
    monkeypatch.setattr(protocol_module.serial.tools.list_ports, "comports", lambda: [])
    protocol, controller = await simulated_controller()
    try:
        with pytest.raises(StartRealModeFailed, match="staying in simulation mode"):
            await controller.start_real_mode()

        assert await controller.simulation_mode() is True
        assert await protocol.get_revision() == "PAACYS00-010-R01"
    finally:
        await protocol.aclose()


async def test_start_real_mode_with_a_silent_scanner_falls_back_to_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    """A port that opens but never answers closes the simulation first; it is reopened so the server stays usable."""
    monkeypatch.setattr(protocol_module, "create_serial_connection", functools.partial(create_mock_connection))
    protocol, controller = await simulated_controller(port="/dev/silent")
    try:
        with pytest.raises(StartRealModeFailed, match="No scanner answered on /dev/silent"):
            await controller.start_real_mode()

        assert await controller.simulation_mode() is True
        assert (await protocol.scan(timeout=1)).data == "012345678905"
    finally:
        await protocol.aclose()


async def test_start_real_mode_and_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scanner that answers on the port is used for real mode; Start Simulation Mode switches back."""
    device = SimulatedScanner()
    monkeypatch.setattr(protocol_module, "create_serial_connection", device.connection_factory())
    protocol, controller = await simulated_controller(port="/dev/cu.usbmodem1201")
    try:
        await controller.start_real_mode()
        assert await controller.simulation_mode() is False

        await controller.start_simulation_mode()
        assert await controller.simulation_mode() is True
    finally:
        await protocol.aclose()


@pytest.mark.parametrize(
    ("switch", "error"),
    [("start_simulation_mode", StartSimulationModeFailed), ("start_real_mode", StartRealModeFailed)],
)
async def test_mode_switch_is_refused_while_scanning(switch: str, error: type[Exception]) -> None:
    """Switching would cut the running scan off, so it is refused and the scan finishes on its own connection."""
    protocol, controller = await simulated_controller(SimulatedScanner(barcode_present=False))
    scan = asyncio.create_task(protocol.scan(timeout=0.5))
    try:
        await asyncio.sleep(0.05)

        with pytest.raises(error, match="scan is in progress"):
            await getattr(controller, switch)()

        with pytest.raises(NoBarcodeDecodedError):
            await scan
        assert await controller.simulation_mode() is True
    finally:
        await protocol.aclose()


def test_mode_switch_errors_are_declared() -> None:
    """An undeclared error reaches clients as an UndefinedExecutionError."""
    controller = SimulationController(ZebraDs8178Protocol(), simulation_mode=True)
    Connector(ZebraDs8178Config()).register(controller)

    assert list(controller.commands["StartRealMode"].errors) == ["StartRealModeFailed"]
    assert list(controller.commands["StartSimulationMode"].errors) == ["StartSimulationModeFailed"]
    assert controller.properties["SimulationMode"].description
