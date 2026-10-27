"""The feature definitions a client downloads: the public interface, which must only change on purpose."""

import pytest
import typing_extensions as typing

FEATURE = "ca.accelerationconsortium/barcodereader/{}/v1"

INTERFACE = {
    "BarcodeReaderController": {
        "commands": {"Scan", "StopScan", "SetScanningEnabled", "StartAiming", "StopAiming"},
        "properties": {"ScannedBarcode", "ScanningEnabled"},
    },
    "BeeperController": {"commands": {"Beep"}, "properties": set()},
    "CradleInformationProvider": {
        "commands": set(),
        "properties": {"Model", "SerialNumber", "ManufactureDate", "FirmwareVersion"},
    },
}
"""Every endpoint of every feature; renaming or removing one breaks existing clients (bump the feature version)."""

STANDARD_FEATURES = {
    "org.silastandard/core/SiLAService/v1",
    "org.silastandard/core/SimulationController/v1",
    "org.silastandard/core/LockController/v2",
}


async def test_server_lists_exactly_the_expected_features(serve) -> None:
    """No feature is missing, and no unexpected connector feature appears."""
    async with serve() as client:
        property_ = client.get_unobservable_property("org.silastandard/core/SiLAService/v1/Property/ImplementedFeatures")
        implemented = set(typing.cast(list[str], await property_.read()))

    assert {FEATURE.format(name) for name in INTERFACE} | STANDARD_FEATURES <= implemented
    assert {f for f in implemented if f.startswith("ca.accelerationconsortium/")} == {
        FEATURE.format(name) for name in INTERFACE
    }


@pytest.mark.parametrize("name", sorted(INTERFACE))
async def test_feature_definition_serves_the_expected_endpoints(serve, name: str) -> None:
    """The downloaded definition has exactly the endpoints listed above."""
    async with serve() as client:
        feature = await client.load_feature(FEATURE.format(name))

    assert set(feature.commands) == INTERFACE[name]["commands"]
    assert set(feature.properties) == INTERFACE[name]["properties"]


async def test_every_endpoint_has_a_description(serve) -> None:
    """Descriptions come from the docstrings; an empty one means a docstring did not parse."""
    async with serve() as client:
        features = [await client.load_feature(FEATURE.format(name)) for name in INTERFACE]

    endpoints = [e for feature in features for e in (*feature.commands.values(), *feature.properties.values())]
    assert all(e.description.strip() for e in [*features, *endpoints])
