"""Calls through a real local SiLA server with the `sila.client` client, as GroundControl or a script would make them."""

import asyncio
import datetime

import pytest

from unitelabs.zebra_ds8178.io import SimulatedScanner
from unitelabs.zebra_ds8178.io import protocol as protocol_module

SIMULATION_CONTROLLER = "org.silastandard/core/SimulationController/v1"
BARCODE_READER = "ca.accelerationconsortium/barcodereader/BarcodeReaderController/v1"
BEEPER = "ca.accelerationconsortium/barcodereader/BeeperController/v1"
CRADLE = "ca.accelerationconsortium/barcodereader/CradleInformationProvider/v1"


async def wait_for_responses(execution) -> dict:
    """Wait for an observable command to finish and return its responses; `get_responses()` alone does not wait."""
    async for _ in await execution.subscribe_status():
        pass
    return await execution.get_responses()


async def scan(client, timeout: float) -> dict:
    command = client.get_observable_command(f"{BARCODE_READER}/Command/Scan")
    return await wait_for_responses(await command.execute({"Timeout": timeout}))


async def test_starts_in_simulation_mode(serve) -> None:
    """A fresh config serves the simulated scanner."""
    async with serve() as client:
        assert await client.get_unobservable_property(f"{SIMULATION_CONTROLLER}/Property/SimulationMode").read() is True


async def test_scan_returns_the_bar_code_as_a_structure(serve) -> None:
    """The symbology arrives as the CDK spells enum members (lowercase, spaces) and the data as text."""
    async with serve() as client:
        responses = await scan(client, timeout=1)

    assert responses["Barcode"] == {"Symbology": "upc a", "Data": "012345678905"}


async def test_out_of_range_timeouts_are_rejected_before_reaching_the_scanner(serve) -> None:
    """The constraint stops a timeout the scanner's own session would cut short."""
    async with serve() as client:
        with pytest.raises(Exception, match="MaximalInclusive|9.9"):
            await scan(client, timeout=30)


async def test_scan_while_disabled_reports_the_defined_error(serve) -> None:
    """The client receives ScanningDisabledError by name, not as an undefined error."""
    async with serve() as client:
        await client.get_unobservable_command(f"{BARCODE_READER}/Command/SetScanningEnabled").execute({"Enabled": False})
        enabled = await client.get_unobservable_property(f"{BARCODE_READER}/Property/ScanningEnabled").read()

        with pytest.raises(Exception, match="Scanning is disabled") as error:
            await scan(client, timeout=1)

    assert enabled is False
    assert type(error.value).__name__ == "ScanningDisabledError"


async def test_stop_scan_ends_a_running_scan_with_the_defined_error(serve, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second client call ends a long Scan; the first receives ScanStoppedError by name."""
    monkeypatch.setattr(protocol_module, "SimulatedScanner", lambda: SimulatedScanner(barcode_present=False))
    async with serve() as client:
        execution = await client.get_observable_command(f"{BARCODE_READER}/Command/Scan").execute({"Timeout": 9.9})
        await asyncio.sleep(0.2)

        await client.get_unobservable_command(f"{BARCODE_READER}/Command/StopScan").execute({})

        with pytest.raises(Exception, match="stopped with `BarcodeReaderController.StopScan`") as error:
            await asyncio.wait_for(wait_for_responses(execution), timeout=3)
    assert type(error.value).__name__ == "ScanStoppedError"


async def test_beep_and_aiming_complete(serve) -> None:
    """The indicator commands take their documented parameters."""
    async with serve() as client:
        await client.get_unobservable_command(f"{BEEPER}/Command/Beep").execute({"Pattern": "short high 1"})
        await client.get_unobservable_command(f"{BARCODE_READER}/Command/StartAiming").execute({})
        await client.get_unobservable_command(f"{BARCODE_READER}/Command/StopAiming").execute({})


async def test_beeper_can_be_silenced_through_a_client(serve) -> None:
    """The settings are sent and read back as a client sees them: a boolean and the tone's name."""
    async with serve() as client:
        await client.get_unobservable_command(f"{BEEPER}/Command/SetBeepAfterScan").execute({"Enabled": False})
        await client.get_unobservable_command(f"{BEEPER}/Command/SetTone").execute({"Tone": "off"})

        beep_after_scan = await client.get_unobservable_property(f"{BEEPER}/Property/BeepAfterScan").read()
        tone = await client.get_unobservable_property(f"{BEEPER}/Property/Tone").read()

    assert (beep_after_scan, tone) == (False, "off")


async def test_cradle_information_is_typed(serve) -> None:
    """The manufacture date arrives as a SiLA Date; the simulated cradle names itself."""
    async with serve() as client:
        serial = await client.get_unobservable_property(f"{CRADLE}/Property/SerialNumber").read()
        made = await client.get_unobservable_property(f"{CRADLE}/Property/ManufactureDate").read()

    assert serial == "SIMULATED"
    assert made == datetime.date(2025, 1, 1)


async def test_start_real_mode_without_a_cradle_stays_in_simulation(serve, monkeypatch: pytest.MonkeyPatch) -> None:
    """The defined error arrives, and the simulated scanner keeps serving scans."""
    monkeypatch.setattr(protocol_module.serial.tools.list_ports, "comports", lambda: [])
    async with serve() as client:
        with pytest.raises(Exception, match="staying in simulation mode") as error:
            await client.get_unobservable_command(f"{SIMULATION_CONTROLLER}/Command/StartRealMode").execute({})

        assert await client.get_unobservable_property(f"{SIMULATION_CONTROLLER}/Property/SimulationMode").read() is True
        assert (await scan(client, timeout=1))["Barcode"]["Data"] == "012345678905"
    assert type(error.value).__name__ == "StartRealModeFailed"
