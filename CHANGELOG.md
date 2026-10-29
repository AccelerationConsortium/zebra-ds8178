# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- SiLA 2 connector for the Zebra DS8178 barcode scanner on its CR8178 cradle, connected over USB with the scanner
  in "SSI over USB CDC" mode. The cradle's serial port is found automatically, or can be set with `serial_port`.
- Barcode Reader Controller: `Scan` reads one bar code, as if the trigger were pulled, and returns its symbology
  and data (timeout 0.5 to 9.9 s), and `StopScan` ends a running scan early. `ScannedBarcode` streams every bar
  code read, including trigger pulls, and ends with `ScannerNotRespondingError` when the connection to the scanner
  is lost or the mode is switched. `SetScanningEnabled` and `ScanningEnabled` turn scanning off and on; a scanner
  that restarts while disabled reads one bar code, after which the connector disables it again.
  `StartAiming` and `StopAiming` switch the red aiming pattern.
- Beeper Controller: `Beep` sounds any of the scanner's 30 beep patterns. `SetBeepAfterScan` turns off the beep
  after each read while error beeps still sound, and `SetTone` sets the pitch or silences the beep after reads
  (`Beep` and the disconnection beep are still heard). Both last until the scanner powers down.
- Cradle Information Provider: the cradle's model, serial number, manufacture date and firmware version.
- Simulation mode, on by default: a simulated scanner answers like the real one, so the connector runs without
  hardware. Switch at runtime with the Simulation Controller; if the real scanner cannot be reached, Start Real
  Mode fails and the connector stays in simulation.
- On every connection the connector makes sure the scanner sends bar codes as SSI packets, writing the setting to
  the scanner only if it differs.
- Hardware checks in `tests/integration`, run with `--serial-port` (and `--with-barcode` to read a bar code).

### Known limitations

- Over USB the cradle answers identity requests itself, so the handheld scanner's own serial number and battery
  state are not available.
- The scanner's LEDs cannot be controlled, and image capture and symbology settings are not exposed.
- The Lock Controller is registered but does not yet protect the commands.
