"""
Guided check of a running DS8178 connector over SiLA 2, for what only ears and eyes can confirm.

Usage:
    uv run connector start            # with "simulation_mode": false in config.json, or call StartRealMode
    uv run python scripts/lab_check.py --port <sila port> [--host 127.0.0.1]

The SILA port is printed by the connector as "Server bound to '0.0.0.0:<port>'". The script walks through Stop Scan,
Beep After Scan and the beeper tone, asks you what you heard or saw at each step, and restores the beeper settings
it found at the end.
"""

# ruff: noqa: T201  # an interactive lab script, which talks to the operator through the terminal

import argparse
import asyncio

import typing_extensions as typing
from sila.client import Client, ClientConfig

SIMULATION_CONTROLLER = "org.silastandard/core/SimulationController/v1"
BARCODE_READER = "ca.accelerationconsortium/barcodereader/BarcodeReaderController/v1"
BEEPER = "ca.accelerationconsortium/barcodereader/BeeperController/v1"


async def read(client: Client, feature: str, name: str) -> typing.Any:
    return await client.get_unobservable_property(f"{feature}/Property/{name}").read()


async def call(client: Client, feature: str, name: str, parameters: dict | None = None) -> None:
    await client.get_unobservable_command(f"{feature}/Command/{name}").execute(parameters or {})


async def scan(client: Client, timeout: float) -> dict:
    """Run Scan and wait for its result; `get_responses()` alone does not wait."""
    execution = await client.get_observable_command(f"{BARCODE_READER}/Command/Scan").execute({"Timeout": timeout})
    async for _ in await execution.subscribe_status():
        pass
    return await execution.get_responses()


def ask(question: str) -> bool:
    return input(f"  {question} [y/n] ").strip().lower().startswith("y")


def step(text: str) -> None:
    input(f"\n{text}\n  press Enter when ready... ")


async def main(host: str, port: int) -> None:
    results: dict[str, bool] = {}
    config = ClientConfig(hostname=host, port=port, tls=False, default_timeout=30_000)
    async with Client(config) as client:
        if await read(client, SIMULATION_CONTROLLER, "SimulationMode"):
            print("The connector is in simulation mode; call StartRealMode or set simulation_mode to false first.")
            return

        beep_after_scan, tone = await read(client, BEEPER, "BeepAfterScan"), await read(client, BEEPER, "Tone")
        print(f"Scanner reports: BeepAfterScan={beep_after_scan}, Tone={tone!r}")
        try:
            step("[1] Stop Scan: keep every bar code away from the scanner window.")
            running = asyncio.ensure_future(scan(client, timeout=9.9))
            await asyncio.sleep(2)
            await call(client, BARCODE_READER, "StopScan")
            try:
                await running
                print("  Scan returned a bar code; it was not stopped.")
            except Exception as error:  # noqa: BLE001  # defined errors arrive as dynamically created classes
                print(f"  Scan ended with {type(error).__name__}: {error}")
            results["Stop Scan turned the scanner's light off after about 2 s, not 9.9 s"] = ask(
                "Did the scanner's light go off after about 2 seconds?"
            )

            await call(client, BEEPER, "SetBeepAfterScan", {"Enabled": False})
            print(f"\n  BeepAfterScan now reads {await read(client, BEEPER, 'BeepAfterScan')}")
            step("[2] Beep After Scan off: hold a bar code in front of the window.")
            print(f"  Scan -> {(await scan(client, timeout=9.9))['Barcode']}")
            results["No beep after a read with BeepAfterScan off"] = ask("Was the read silent?")
            step("[3] Pull the trigger on a bar code once, then press Enter.")
            results["No beep after a trigger pull with BeepAfterScan off"] = ask("Was the trigger read silent?")

            await call(client, BEEPER, "SetBeepAfterScan", {"Enabled": True})
            step("[4] Beep After Scan on again: pull the trigger on a bar code once.")
            results["Beep after a read is back with BeepAfterScan on"] = ask("Did that read beep?")

            await call(client, BEEPER, "SetTone", {"Tone": "off"})
            print(f"\n  Tone now reads {await read(client, BEEPER, 'Tone')!r}")
            await call(client, BEEPER, "Beep", {"Pattern": "short high 3"})
            results["Beep is still heard with the tone off"] = ask(
                "Three beeps were just requested; did you hear them?"
            )
            step("[5] Tone off: pull the trigger on a bar code once.")
            results["Reads are silent with the tone off"] = ask("Was that read silent?")

            await call(client, BEEPER, "SetTone", {"Tone": "medium"})
            await call(client, BEEPER, "Beep", {"Pattern": "short high 3"})
            results["Beep is heard again with the tone medium"] = ask(
                "Three beeps were just requested; did you hear them?"
            )
        finally:
            await call(client, BEEPER, "SetBeepAfterScan", {"Enabled": beep_after_scan})
            await call(client, BEEPER, "SetTone", {"Tone": tone})
            print(f"\nRestored BeepAfterScan={beep_after_scan}, Tone={tone!r}.")

    print("\nResults:")
    for check, passed in results.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {check}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.host, arguments.port))
