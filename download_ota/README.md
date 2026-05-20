# Daylight DC-1 OTA Download Tool

Python script to directly download OTA updates for the Daylight DC-1 tablet.

## Setup

The script will automatically install dependencies when run with `uv`:

```bash
uv run download_ota.py --help
```

## Required parameters

Both `--device-id` and `--os-version` are **required**. The server only returns
an OS image when you give it the device's current build to compare against, so
there is no useful "no version" mode — the tool refuses to run without them
(unless you use `--adb`).

These values mirror exactly what the real `com.ota.manager` app sends:

| Param | How to get it |
|-------|---------------|
| `--device-id` | `adb shell getprop ro.serialno` |
| `--os-version` | First non-empty of: `adb shell getprop gsm.sn1`, then `adb shell getprop ro.mediatek.version.release`, then `adb shell getprop ro.serialno` |

`--os-version` is a MediaTek build string like `0.9.9.58.prod.2-1407` — **not a
date**. (The old `20231005.1937` example was wrong.)

## Usage

### Download the latest OTA update:

```bash
uv run download_ota.py --device-id YOUR_SERIAL --os-version "0.9.9.58.prod.2-1407"
```

The file is named from the response's `version` field, e.g.
`ota-0.9.9.58.prod.2-1407.zip`.

### Let the tool read both values off a connected device:

```bash
uv run download_ota.py --adb
```

`--adb` fills in any unspecified `--device-id` / `--os-version` by running the
`getprop` chain above against the connected device. You can still override
either explicitly, e.g. `--adb --os-version "..."`.

### Custom output location:

```bash
uv run download_ota.py --adb --output /path/to/ota.zip
```

### Just get the URL without downloading:

```bash
uv run download_ota.py --adb --no-download
```

### Enable debug logging for troubleshooting:

```bash
uv run download_ota.py --adb --debug
```

Debug mode shows detailed information about:
- All HTTP requests and responses
- Request/response headers and bodies
- Encryption process details
- Token generation steps
- Download progress details
- Full error tracebacks

## How It Works

1. Gets server timestamp from `https://updates.daylight.ink/device/time`
2. Encrypts `deviceID:timestamp` with RSA public key
3. Registers device at `/device/register` to get access token
4. Calls `/device/check-update` to get pre-signed S3 URL
5. Downloads OTA zip directly from S3 (URL valid for 12 hours)

## Troubleshooting

### General debugging

For any issues, run with `--debug` flag to see detailed logs:

```bash
uv run download_ota.py --device-id YOUR_SERIAL_HERE --debug 2> debug.log
```

This will show:
- Complete request/response details
- Encryption process information
- Exact error messages and stack traces

### No updates available

If `data.url` comes back empty, the device is already on the latest OS build for
the `--os-version` you supplied (the tool will say so explicitly, and will also
list any APK-only updates it saw). Try:
- Confirming `--os-version` is the real current build (run the `getprop` chain,
  or use `--adb`); a wrong/blank-equivalent value makes the server return nothing
- Checking again later
- Using `--debug` to see the exact server response

### Authentication errors

- Verify your device serial is correct (check `adb shell getprop ro.serialno`)
- Check that `https://updates.daylight.ink` is accessible
- Use `--debug` to see the encrypted passphrase and registration request/response

### Download errors

- The S3 URL is valid for 12 hours
- Check your internet connection
- Ensure you have write permissions for the output directory
- Use `--debug` to see response headers and download progress details
