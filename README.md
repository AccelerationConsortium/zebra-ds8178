# Zebra DS8178

A SiLA 2 connector for the Zebra DS8178 cordless 1D/2D barcode scanner on its CR8178 cradle, built with the
UniteLabs CDK. It talks to the scanner through the cradle's USB serial port with Zebra's Simple Serial Interface
(SSI), and runs without hardware against a simulated scanner.

## Status

Scanning, the beeper, the aiming pattern, enabling and disabling scanning, and reading the cradle's identity are
implemented, tested against the simulated scanner, and checked on a DS8178 with a CR8178-SC cradle. The hardware
checks are in `tests/integration`; see [Checking a real scanner](#checking-a-real-scanner).

## Features

All features are published as `ca.accelerationconsortium/barcodereader/<Feature>/v1`.

| Feature | Endpoint | Kind | What it does |
| --- | --- | --- | --- |
| Barcode Reader Controller | Scan | observable command | Read one bar code, as if the trigger were pulled, and return its symbology and data; timeout 0.5 to 9.9 s; cancellable |
| | ScannedBarcode | observable property | Every bar code read from now on, by Scan or by pulling the trigger |
| | ScanningEnabled, SetScanningEnabled | property, command | Whether the scanner reads bar codes at all |
| | StartAiming, StopAiming | commands | Switch the red aiming pattern on and off |
| Beeper Controller | Beep | command | Sound one of the 30 documented beep patterns |
| Cradle Information Provider | Model, SerialNumber, ManufactureDate, FirmwareVersion | properties | Identify the CR8178 cradle |
| Simulation Controller | StartRealMode, StartSimulationMode, SimulationMode | standard SiLA | Switch between the simulated and the real scanner at runtime |

Scan refuses with `ScanningDisabledError` while scanning is disabled and with `ScannerBusyError` while another scan
runs, and reports `NoBarcodeDecodedError` when the timeout passes. Endpoints that talk to the scanner report
`ScannerNotRespondingError` when it does not answer.

## Connect to the real scanner

1. Plug the cradle's USB cable into the computer. The cable's square plug at the cradle end carries USB, not
   Ethernet.
2. On Windows only, install the Zebra USB CDC driver first (zebra.com/support > Barcode Scanners > USB CDC
   Driver). Without it the scanner can stall at power-up (Product Reference Guide p. 8-5).
3. Pair the scanner to the cradle by scanning the bar code on the cradle.
4. Scan the "SSI over USB CDC" programming bar code (Product Reference Guide p. 8-6). Out of the box the scanner
   types bar codes like a keyboard and cannot be controlled. After rebooting it appears as a serial port:
   `/dev/cu.usbmodem*` on macOS, `/dev/ttyACM*` on Linux, `COM*` on Windows.
5. Start with `"simulation_mode": false` in `config.json`, or call `SimulationController.StartRealMode` from a
   client. Leave `serial_port` empty to find the one connected cradle by its Zebra USB ID, or set it when several
   are connected.

On every connection the connector checks that the scanner sends bar codes as SSI packets. If not, it switches the
setting once and stores it on the scanner (parameter 238). If the scanner does not answer, Start Real Mode fails
with `StartRealModeFailed` and the connector stays in simulation.

## Getting Started

### Prerequisites

Ensure that Python 3.10 or newer is installed on your system. You can download Python from the official website [python.org](https://www.python.org/downloads/).

### Installation

#### Create a Virtual Environment

It is highly recommended to use a virtual environment to manage the dependencies for your connector project. This keeps the dependencies for different connectors isolated from each other. Use the following command to create a virtual environment:

```sh
python -m venv venv
```

Activate the virtual environment:

- On **Windows**:

  ```sh
  .\venv\Scripts\activate.bat
  ```

- On **macOS**/**Linux**:

  ```sh
  source venv/bin/activate
  ```

If you are on a Windows machine, you may additionally wish to set the `UNITELABS_CDK_APP` environment variable to the connector's entry point. This can be done by running the following command:

```sh
set UNITELABS_CDK_APP=unitelabs.zebra_ds8178:create_app
```

Setting this environment variable will allow you to run varous CLI commands without providing `--app unitelabs.zebra_ds8178:create_app` every time.

#### Install Required Dependencies

Install the necessary Python packages into your active virtual environment. Use pip to download the connector along with its dependencies:

```sh
python -m pip install unitelabs-zebra-ds8178 \
  --index-url https://gitlab.com/api/v4/groups/1009252/-/packages/pypi/simple
```

If you are working with a private connector repository, authenticate pip to allow access:

```sh
python -m pip install unitelabs-zebra-ds8178 \
  --index-url https://<username>:<password>@gitlab.com/api/v4/groups/1009252/-/packages/pypi/simple
```

#### Configure the Connector

To get information about the configuration values for the connector simply run:

- On **Windows**:

  ```sh
  config show --app unitelabs.zebra_ds8178:create_app
  ```

- On **macOS**/**Linux**:

  ```sh
  config show
  ```

To create a configuration file for our connector we run:

- On **Windows**:

  ```sh
  config create --app unitelabs.zebra_ds8178:create_app
  ```

- On **macOS**/**Linux**:

  ```sh
  config create
  ```

Used as such this command will create a `config.json` in the current working directory. If you prefer to use yaml for your config file or would like to save the file to a different location, simply add the `--path` argument:

- On **Windows**:

  ```sh
  config create --app unitelabs.zebra_ds8178:create_app  --path <path to config>
  ```

- On **macOS**/**Linux**:

  ```sh
  config create --path <path to config>
  ```

The file that is created will be populated with default configuration values that you may now edit.

Note: The `cloud_server_endpoint` values are only necessary if you want to use the connector with the UniteLabs platform.

#### Verify the Installation

After installation, you can verify that everything works by starting the connector using the CLI tool included in the dependencies:

Note: This must be done in the activated environment setup in Step 1.

```sh
connector start --app unitelabs.zebra_ds8178:create_app -vvv
```

If you decided to create your configuration file at a non-default location, you can specify it with the `--config-path` or `-cfg` argument:

```sh
connector start --app unitelabs.zebra_ds8178:create_app -cfg <path to config> -vvv
```

In this way one can have multiple configurations for the same connector.

## Usage

To interact with the running connector, we recommend using the [SiLA Browser](https://gitlab.com/unitelabs/sila2/sila-browser).

### Encryption

To secure communication between the connector and its clients, you can enable TLS encryption. Start by installing the optional `cryptography` package for generating TLS certificates:

```sh
python -m pip install cryptography
```

To generate a pair of public and private keys, use the following command:

```sh
certificate generate
```

Without any arguments this command uses the default config location to get the connector's UUID and host name, required to generate TLS certificates. It will prompt you as to whether or not you want to update your config file to enable TLS encryption on your connector. This prompt can be suppressed with the use of the `--non-interactive` or `-y` flag, which will update the config file with paths to the locally created files without prompting, or with `--embed` or `-e` flag to write the file contents into the config file directly.

You can adjust your config file's host to reflect the machine's hostname, or set it to `localhost` if the connector should only be accessible locally.

If your config file was created at a non-default path, you can provide it with the `--config-path` or `-cfg` option:

```sh
certificate generate -cfg <path to config>
```

By default the generate command creates the `cert.pem` and `key.pem` files in your current working directory. You can customize the output directory with the `--target` argument.

If you choose not to update your config file with the paths to your certificates using `--non-interactive` or `-y` or to have the file contents saved directly in your config file with `--embed` or `-e` flag, you will have to modify the config file yourself to set the following values under `sila_server`:
 
- `certificate_chain` - the path to the `cert.pem` file
- `private_key` - the path to the `key.pem` file
- `tls` - a boolean that toggles on/off TLS encryption for the SiLA server

With your updated config file you can once again run:

```sh
connector start --app unitelabs.zebra_ds8178:create_app -vvv
```

> **Important:** Never share the `key.pem` file with anyone. Only the `cert.pem` is required for clients to connect to encrypted servers.

## Limitations

- Over USB CDC the connector talks to the cradle, which passes scans, beeps and aiming through to the scanner but
  answers attribute requests itself. The handheld scanner's own serial number and battery state are therefore not
  available, and Cradle Information Provider reports the cradle's values.
- The scanner's LEDs cannot be controlled: it acknowledges the SSI LED commands but lights nothing.
- Scan waits at most 9.9 s, the scanner's longest decode session.
- A bar code read by pulling the trigger while Scan waits is returned by Scan, because the scanner does not say
  what started a read.
- The simulated scanner does not simulate trigger pulls, so ScannedBarcode only reports bar codes read by Scan in
  simulation mode.
- Image capture, symbology settings and the other scanner parameters are not exposed.
- The Lock Controller is registered but does not yet protect the commands.

## Contribute

We welcome contributions to improve our connectors. Follow the steps below to set up your development environment.

### Development Environment

We use [uv][] for Python packaging.

#### Set Up the Environment

Install and configure `uv`:

```sh
pipx install uv
```

Note: requires `uv>=0.6.8`.

#### Install the Package

Clone the repository:

```sh
git clone https://github.com/AccelerationConsortium/zebra-ds8178.git
```

Set up the development environment and start the connector:

```sh
uv sync --all-extras
uv run connector start -vvv
```

Note: By default, uv will sync all dependencies with every call to `uv run CMD`. To prevent this use `uv run --frozen CMD` or set the environment variable `UV_FROZEN=true`.

#### Install pre-commit Hooks

Set up `pre-commit` hooks to ensure code quality:

```sh
uv run pre-commit install
```

#### Running Tests

To run the test suite:

```sh
uv run pytest
```

To run tests with a specific version of python, e.g. Python 3.12:

```sh
uv run --python 3.12 --all-extras pytest
```

#### Checking a real scanner

`tests/integration` checks the connector against a real scanner and is skipped unless you pass the cradle's
serial port:

```sh
uv run pytest tests/integration --serial-port /dev/cu.usbmodem1201 -s
```

The tests read the cradle, sound one beep, flash the aiming pattern, and disable scanning only to enable it again.
To also read a bar code, hold one in front of the scanner window and add `--with-barcode`.

#### Dev-Mode

To improve the development experience, we recommend running the connector in developer mode. This will automatically reload the connector whenever changes to the source code are saved.

```sh
uv run connector dev --app unitelabs.zebra_ds8178:create_app
```

## Contact

If you found a bug, please use the [issue tracker][issue-tracker].

[issue-tracker]: https://github.com/AccelerationConsortium/zebra-ds8178/issues
[uv]: https://docs.astral.sh/uv/
