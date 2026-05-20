"""
Daylight DC-1 OTA Download Tool

Authenticates with the Daylight OTA update server and downloads the latest OTA package.

Both --device-id and --os-version are REQUIRED, because the server only returns
an OS image when you tell it the device's current build so it can compare. There
is no useful "no version" mode, so the tool will not run without them (unless
--adb is used to read them off a connected device).

How to get the values (these mirror what the real com.ota.manager app sends):

  --device-id   adb shell getprop ro.serialno

  --os-version  The app's getBuildVersion() reads, in order:
                  1. adb shell getprop gsm.sn1
                  2. adb shell getprop ro.mediatek.version.release   (fallback)
                  3. adb shell getprop ro.serialno                   (last resort)
                Use the first one that returns a non-empty value.
                NOTE: this is a MediaTek build string like
                "0.9.9.58.prod.2-1407" -- NOT a date.

Usage:
  uv run download_ota.py --device-id <serial> --os-version "0.9.9.58.prod.2-1407"
  uv run download_ota.py --adb          # read device-id + os-version off device
  uv run download_ota.py --adb --os-version "0.9.9.58.prod.2-1407"   # override
  uv run download_ota.py --device-id <serial> --os-version <ver> --output ./x.zip

Without --adb, omitting --device-id or --os-version is an error.
"""

import argparse
import base64
import json
import re
import subprocess
import sys
import logging
import requests
from pathlib import Path
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

BASE_URL = "https://updates.daylight.ink"

RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuwSmSQXERDQ3dv1Fi9Wn
+9jS4tTJ/8OLxK1CkWyB6vAxld2wqnItOmWY2vFiKQwmfBrLzwMgoKJggdAnBVIB
UNiy6kHCBe0o/aQyCd7z+yo7Q58Z2cXIGBa4DfX3jibWTFoHhz0EjAEoV69pQGz/
b+XOaxPQPsmWbvKfWo+5cOG/LzDh+5ZWAqIxwDCK0G97Ysz2pwfWiHyPFySObY+p
R5oECS9VuaWEXYDR1pkF7IhsXV2lMZWIQMRVMQ3a1Q7JfI2u4ScttRQN3GJn84kT
YLPZrNWOg67AbHvyeylA8Og3W2qsNabaVaxr12EvZJw9JTyWxcgpXXIKSmg46GMb
zQIDAQAB
-----END PUBLIC KEY-----"""

parser = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter
)
parser.add_argument(
    "--device-id",
    default=None,
    help="Device serial number. Get via: adb shell getprop ro.serialno. "
         "Required unless --adb is given."
)
parser.add_argument(
    "--os-version",
    default=None,
    help="Current OS build string (e.g. \"0.9.9.58.prod.2-1407\"). Get via: "
         "adb shell getprop gsm.sn1 (fallback: ro.mediatek.version.release, "
         "then ro.serialno) -- use the first non-empty value. Required unless "
         "--adb is given. Not a date."
)
parser.add_argument(
    "--adb",
    action="store_true",
    help="Read any unspecified --device-id / --os-version off a connected "
         "device via adb getprop (same property chain the OTA app uses)."
)
parser.add_argument(
    "--output",
    default=None,
    help="Output filepath for the downloaded OTA package. Default: "
         "ota-<version>.zip, where <version> is the response's version field."
)
parser.add_argument(
    "--no-download",
    action="store_true",
    help="Only fetch the URL, don't download the file"
)
parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable debug logging for troubleshooting"
)


def adb_getprop(prop):
    """Return `adb shell getprop <prop>` stripped, or '' on failure/empty."""
    try:
        result = subprocess.run(
            ["adb", "shell", "getprop", prop],
            capture_output=True, text=True, timeout=15
        )
    except FileNotFoundError:
        raise RuntimeError("adb not found on PATH (required for --adb)")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"adb timed out reading getprop {prop}")

    if result.returncode != 0:
        raise RuntimeError(
            f"adb getprop {prop} failed (is a device connected?): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    value = result.stdout.strip()
    logging.debug(f"adb getprop {prop} -> {value!r}")
    return value


def adb_resolve_os_version():
    """Mirror com.ota.manager getBuildVersion(): gsm.sn1, then
    ro.mediatek.version.release, then ro.serialno (Build.SERIAL)."""
    for prop in ("gsm.sn1", "ro.mediatek.version.release", "ro.serialno"):
        value = adb_getprop(prop)
        if value:
            logging.debug(f"Resolved os_version from {prop}")
            return value
    return ""


def encrypt_passphrase(device_id, timestamp):
    """Encrypt the passphrase using RSA public key."""
    passphrase = f"{device_id}:{timestamp}"
    logging.debug(f"Creating passphrase: {passphrase}")

    logging.debug("Loading RSA public key...")
    public_key = RSA.import_key(RSA_PUBLIC_KEY)
    logging.debug(f"RSA key size: {public_key.size_in_bits()} bits")

    logging.debug("Encrypting passphrase with RSA/ECB/PKCS1Padding...")
    cipher = PKCS1_v1_5.new(public_key)
    encrypted = cipher.encrypt(passphrase.encode('utf-8'))
    logging.debug(f"Encrypted bytes length: {len(encrypted)}")

    encoded = base64.b64encode(encrypted).decode('utf-8')
    logging.debug(f"Base64 encoded passphrase (length={len(encoded)}): {encoded[:50]}...")
    return encoded


def get_server_time():
    """Get server timestamp."""
    print("📡 Getting server time...")
    url = f"{BASE_URL}/device/time"
    logging.debug(f"GET {url}")

    try:
        response = requests.get(url)
        logging.debug(f"Response status: {response.status_code}")
        logging.debug(f"Response headers: {dict(response.headers)}")

        response.raise_for_status()

        raw_text = response.text
        logging.debug(f"Response body: {raw_text}")

        data = response.json()
        logging.debug(f"Parsed JSON: {json.dumps(data, indent=2)}")

        timestamp = data.get("data", {}).get("time")

        if not timestamp:
            logging.error("No 'time' field found in response data")
            raise ValueError("Failed to get server timestamp")

        print(f"✓ Server time: {timestamp}")
        return timestamp

    except requests.exceptions.RequestException as error:
        logging.error(f"Request failed: {error}")
        raise


def register_device(device_id, encrypted_passphrase):
    """Register device and get access token."""
    print("🔐 Registering device...")

    url = f"{BASE_URL}/device/register"
    payload = {
        "device_id": device_id,
        "passphrase": encrypted_passphrase
    }

    logging.debug(f"POST {url}")
    logging.debug(f"Request payload: {json.dumps(payload, indent=2)}")

    headers = {"Content-Type": "application/json"}
    logging.debug(f"Request headers: {headers}")

    try:
        response = requests.post(url, json=payload, headers=headers)
        logging.debug(f"Response status: {response.status_code}")
        logging.debug(f"Response headers: {dict(response.headers)}")

        raw_text = response.text
        logging.debug(f"Response body: {raw_text}")

        response.raise_for_status()

        data = response.json()
        logging.debug(f"Parsed JSON: {json.dumps(data, indent=2)}")

        access_token = data.get("data", {}).get("access_token")
        refresh_token = data.get("data", {}).get("refresh_token")

        if not access_token:
            logging.error("No 'access_token' field found in response data")
            raise ValueError("Failed to get access token")

        logging.debug(f"Access token (first 20 chars): {access_token[:20]}...")
        if refresh_token:
            logging.debug(f"Refresh token (first 20 chars): {refresh_token[:20]}...")

        print("✓ Registered successfully")

        token_with_device = f"{device_id}:{access_token}"
        logging.debug(f"Creating bearer token: {device_id}:<access_token>")

        bearer_token = base64.b64encode(token_with_device.encode('utf-8')).decode('utf-8')
        logging.debug(f"Bearer token (first 30 chars): {bearer_token[:30]}...")

        return bearer_token

    except requests.exceptions.RequestException as error:
        logging.error(f"Registration request failed: {error}")
        raise


def check_for_updates(bearer_token, os_version):
    """Check for available updates."""
    print("🔍 Checking for updates...")

    url = f"{BASE_URL}/device/check-update"
    payload = {
        "os_version": os_version,
        "apks": []
    }

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }

    logging.debug(f"POST {url}")
    logging.debug(f"Request payload: {json.dumps(payload, indent=2)}")
    logging.debug(f"Authorization header: Bearer {bearer_token[:30]}...")

    try:
        response = requests.post(url, json=payload, headers=headers)
        logging.debug(f"Response status: {response.status_code}")
        logging.debug(f"Response headers: {dict(response.headers)}")

        raw_text = response.text
        logging.debug(f"Response body: {raw_text}")

        response.raise_for_status()

        data = response.json()
        logging.debug(f"Parsed JSON: {json.dumps(data, indent=2)}")

        update_data = data.get("data", {})
        update_url = update_data.get("url")
        apk_updates = update_data.get("apk_updates", []) or []

        if apk_updates:
            logging.debug(f"{len(apk_updates)} APK update(s) present in response")
            print(f"ℹ️  {len(apk_updates)} APK update(s) available (not OS images, skipped):")
            for apk in apk_updates:
                print(f"     - {apk.get('package_name')} v{apk.get('version')} ({apk.get('file_name')})")

        if not update_url:
            logging.info("No OS update URL in response (data.url is empty)")
            if apk_updates:
                print("✗ No OS OTA image available for this os_version "
                      "(only APK updates were returned)")
            else:
                print("✗ No updates available")
            return None

        update_version = update_data.get("version") or ""
        logging.debug(f"OS update URL: {update_url}")
        logging.debug(f"OS update version: {update_version!r}")
        print("✓ OS update found!" + (f" (version {update_version})" if update_version else ""))
        return update_url, update_version

    except requests.exceptions.RequestException as error:
        logging.error(f"Check-update request failed: {error}")
        raise


def download_file(url, output_filepath):
    """Download file from URL with progress indication."""
    print(f"📥 Downloading to: {output_filepath}")

    logging.debug(f"GET {url}")

    try:
        response = requests.get(url, stream=True)
        logging.debug(f"Response status: {response.status_code}")
        logging.debug(f"Response headers: {dict(response.headers)}")

        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))
        logging.debug(f"Content-Length: {total_size} bytes ({total_size / (1024 * 1024):.2f} MB)")

        output_path = Path(output_filepath)
        logging.debug(f"Output path: {output_path.absolute()}")

        with open(output_path, 'wb') as file:
            if total_size == 0:
                logging.warning("No Content-Length header - downloading entire response")
                file.write(response.content)
            else:
                downloaded = 0
                chunk_size = 8192
                logging.debug(f"Downloading in chunks of {chunk_size} bytes")

                for chunk in response.iter_content(chunk_size=chunk_size):
                    if chunk:
                        file.write(chunk)
                        downloaded += len(chunk)
                        percent = (downloaded / total_size) * 100

                        mb_downloaded = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)

                        print(f"\r  Progress: {percent:.1f}% ({mb_downloaded:.1f}/{mb_total:.1f} MB)", end='', flush=True)

                print()
                logging.debug(f"Total downloaded: {downloaded} bytes")

        file_size = output_path.stat().st_size
        logging.debug(f"File size on disk: {file_size} bytes")

        print(f"✓ Download complete: {output_path.absolute()}")
        return output_path

    except requests.exceptions.RequestException as error:
        logging.error(f"Download request failed: {error}")
        raise
    except IOError as error:
        logging.error(f"File write error: {error}")
        raise


def main():
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='[%(levelname)s] %(message)s',
            stream=sys.stderr
        )
        logging.debug("Debug logging enabled")
    else:
        logging.basicConfig(
            level=logging.WARNING,
            format='[%(levelname)s] %(message)s',
            stream=sys.stderr
        )

    try:
        device_id = args.device_id
        os_version = args.os_version

        if args.adb:
            if not device_id:
                print("📲 Reading device-id from device (adb getprop ro.serialno)...")
                device_id = adb_getprop("ro.serialno")
            if not os_version:
                print("📲 Reading os-version from device (adb getprop chain)...")
                os_version = adb_resolve_os_version()

        missing = []
        if not device_id:
            missing.append("--device-id")
        if not os_version:
            missing.append("--os-version")
        if missing:
            print(
                f"\n❌ Missing required argument(s): {', '.join(missing)}\n"
                "   Pass them explicitly, or use --adb to read them from a "
                "connected device.\n"
                "   --device-id : adb shell getprop ro.serialno\n"
                "   --os-version: adb shell getprop gsm.sn1 "
                "(fallback ro.mediatek.version.release, then ro.serialno)",
                file=sys.stderr,
            )
            return 2

        logging.debug(f"Arguments: device_id={device_id}, os_version={os_version}, output={args.output}, adb={args.adb}, no_download={args.no_download}")

        print(f"🔧 Device ID: {device_id}")
        print(f"🔧 OS Version: {os_version}")
        print()

        timestamp = get_server_time()

        encrypted = encrypt_passphrase(device_id, timestamp)

        bearer_token = register_device(device_id, encrypted)

        result = check_for_updates(bearer_token, os_version)

        if not result:
            print("\nNo updates available. Try:")
            print("  - Different OS version")
            print("  - Checking again later")
            return 0

        update_url, update_version = result

        print(f"\n📎 S3 URL:\n{update_url}\n")

        if args.no_download:
            print("✓ URL fetched (skipping download as requested)")
            return 0

        if args.output:
            output_filepath = args.output
        elif update_version:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", update_version)
            output_filepath = f"ota-{safe}.zip"
        else:
            output_filepath = "daylight-ota.zip"
            logging.warning(
                "Response had no version field; using default output name"
            )

        download_file(update_url, output_filepath)

        print("\n✅ Success! OTA package downloaded.")

    except requests.exceptions.RequestException as error:
        print(f"\n❌ Network error: {error}", file=sys.stderr)
        logging.error(f"Full error details: {error!r}")

        if hasattr(error, 'response') and error.response is not None:
            logging.error(f"Response status code: {error.response.status_code}")
            logging.error(f"Response headers: {dict(error.response.headers)}")
            try:
                error_data = error.response.json()
                print(f"Server response: {json.dumps(error_data, indent=2)}", file=sys.stderr)
            except Exception:
                print(f"Response text: {error.response.text}", file=sys.stderr)

        if hasattr(error, 'request') and error.request is not None:
            logging.error(f"Request method: {error.request.method}")
            logging.error(f"Request URL: {error.request.url}")
            logging.error(f"Request headers: {dict(error.request.headers)}")

        return 1
    except Exception as error:
        print(f"\n❌ Error: {error}", file=sys.stderr)
        logging.error(f"Full error details: {error!r}")
        logging.exception("Exception traceback:")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
