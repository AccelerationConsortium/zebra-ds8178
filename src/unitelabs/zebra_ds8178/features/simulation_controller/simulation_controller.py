import typing_extensions as typing

from unitelabs.cdk.features.core.simulation_controller import (
    SimulationControllerBase,
    StartRealModeFailed,
    StartSimulationModeFailed,
)

from ...io import ScannerBusyError, ScannerNotRespondingError, SerialPortNotFoundError, ZebraDs8178Protocol


# The CDK reads descriptions and declared errors from the overriding methods, not from SimulationControllerBase, so the
# docstrings of the standard SiLA feature are repeated here verbatim; without them the errors are sent as undefined.
# The base's SiLA decorators turn its methods into validatable objects, which plain overrides cannot match in type.
class SimulationController(SimulationControllerBase):
    """
    This Feature provides control over the simulation behaviour of a SiLA Server.

    A SiLA Server can run in two modes:
    (a) Real Mode - with real activities, e.g. addressing or controlling real hardware,
        e.g. through serial/CANBus commands, writing to real databases, moving real objects etc.
    (b) Simulation Mode - where every command is only simulated and responses are just example returns.

    Note that certain commands and properties might not be affected by this feature if they
    do not interact with the real world.
    """

    def __init__(self, protocol: ZebraDs8178Protocol, simulation_mode: bool):
        super().__init__()
        self.protocol = protocol
        self._simulation_mode = simulation_mode

    @typing.override
    async def start_simulation_mode(self) -> None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """
        Set the SiLA Server to run in Simulation Mode, i.e. all following commands are executed in simulation mode.

        The Simulation Mode can only be entered, if all hardware operations have been safely terminated
        or are in a controlled, safe state.

        The simulation mode can be stopped by issuing the 'Start Real Mode' command.

        Raises:
          StartSimulationModeFailed: If the server cannot change to Simulation Mode.
        """
        try:
            await self.protocol.open_simulation()
        except ScannerBusyError as error:
            raise StartSimulationModeFailed(str(error)) from error

        self._simulation_mode = True

    @typing.override
    async def start_real_mode(self) -> None:  # pyright: ignore[reportIncompatibleVariableOverride]
        """
        Set the SiLA Server to run in real mode.

        In real-mode all following commands are executed with real-world
        interactions, like serial port/CAN communication, motor actions etc.

        If the server is in Simulation Mode it can be interrupted at any time. A re-initialization of
        the hardware might be required. The Real Mode can be stopped by issuing the 'Start Simulation Mode' command.

        Raises:
          StartRealModeFailed: If the server cannot change to Real Mode.
        """
        try:
            await self.protocol.open_device()
        except ScannerBusyError as error:
            # Raised before the current connection is closed, so the running mode continues untouched.
            raise StartRealModeFailed(str(error)) from error
        except (SerialPortNotFoundError, ScannerNotRespondingError) as error:
            # The previous connection may already be closed; reopening the simulation keeps the server usable.
            await self.protocol.open_simulation()
            self._simulation_mode = True
            msg = f"Could not reach the scanner ({error}); staying in simulation mode."
            raise StartRealModeFailed(msg) from error

        self._simulation_mode = False

    @typing.override
    async def simulation_mode(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Whether or not the SiLA Server is in Simulation Mode."""
        return self._simulation_mode
