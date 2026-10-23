"""Identifying the cradle through the SiLA feature."""

import datetime

import pytest

from unitelabs.zebra_ds8178.features import CradleInformationProvider
from unitelabs.zebra_ds8178.io import ScannerNotRespondingError


async def test_reports_the_simulated_cradle(simulated) -> None:
    """Each property returns one typed value; the simulated cradle is marked as such."""
    provider = CradleInformationProvider(await simulated())

    assert await provider.get_model() == "CR8178-SC100F4WW"
    assert await provider.get_serial_number() == "SIMULATED"
    assert await provider.get_manufacture_date() == datetime.date(2025, 1, 1)
    assert await provider.get_firmware_version() == "PAACYS00-010-R01"


async def test_a_lost_connection_is_reported_as_not_responding(simulated) -> None:
    """Without a connection, the defined error points the operator to Start Real Mode."""
    protocol = await simulated()
    provider = CradleInformationProvider(protocol)
    await protocol.aclose()

    with pytest.raises(ScannerNotRespondingError, match="Start Real Mode"):
        await provider.get_model()


def test_every_property_declares_not_responding(registered) -> None:
    """All four values are read from the cradle, so each can fail when it does not answer."""
    provider = registered(CradleInformationProvider)

    assert {name: list(prop.errors) for name, prop in provider.properties.items()} == {
        name: ["ScannerNotRespondingError"] for name in ("FirmwareVersion", "ManufactureDate", "Model", "SerialNumber")
    }
    assert provider.properties["ManufactureDate"].data_type.__name__ == "Date"
