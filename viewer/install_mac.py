"""Install the MVPDF Viewer app (opens .mvpdf files) and the browser extension's native host.

    python viewer/install_mac.py              install (re-run after moving this repo)
    python viewer/install_mac.py --uninstall  remove both

Run it with the Python that has the converter's dependencies; that interpreter is baked
into the app so double-clicking a PDF "Open With > MVPDF Viewer" can convert it too.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_NAME = "MVPDF Viewer"
BUNDLE_ID = "com.mvpdf.viewer"
DOC_UTI = "com.mvpdf.document"
APP_PATH = Path.home() / "Applications" / f"{APP_NAME}.app"
VIEWER_SCRIPT = Path(__file__).resolve().parent / "mvpdf_viewer.py"
LOG_FILE = Path.home() / "Library" / "Logs" / "MVPDF" / "app.log"
EXTENSION_DIR = Path(__file__).resolve().parents[1] / "extension"
NATIVE_HOST_SCRIPT = Path(__file__).resolve().parent / "native_host.py"
NATIVE_HOST_NAME = "com.mvpdf.host"
SUPPORT_DIR = Path.home() / "Library" / "Application Support"
HOST_LAUNCHER = SUPPORT_DIR / "MVPDF" / "mvpdf-native-host"
# Chrome always; the others only when that browser is installed.
BROWSER_DIRS = ("Google/Chrome", "Chromium", "BraveSoftware/Brave-Browser", "Microsoft Edge", "Vivaldi")
LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


def applescript_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def applet_source(python: str) -> str:
    """AppleScript that forwards opened files to mvpdf_viewer.py in the background."""
    command = (
        f"quoted form of {applescript_quote(python)} & \" \" & "
        f"quoted form of {applescript_quote(str(VIEWER_SCRIPT))}"
    )
    log = f"quoted form of {applescript_quote(str(LOG_FILE))}"
    return f"""
on open theItems
    repeat with anItem in theItems
        do shell script "mkdir -p " & quoted form of {applescript_quote(str(LOG_FILE.parent))} & "; " & {command} & " open " & quoted form of (POSIX path of anItem) & " >> " & {log} & " 2>&1 &"
    end repeat
end open

on run
    do shell script {command} & " home >> " & {log} & " 2>&1 &"
end run
"""


def document_types() -> list[dict]:
    return [
        {
            "CFBundleTypeName": "MVPDF Document",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Owner",
            "LSItemContentTypes": [DOC_UTI],
        },
        {   # offered under "Open With" for PDFs, never made the default
            "CFBundleTypeName": "PDF Document",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["com.adobe.pdf"],
        },
    ]


def exported_types() -> list[dict]:
    return [{
        "UTTypeIdentifier": DOC_UTI,
        "UTTypeDescription": "MVPDF Document",
        "UTTypeConformsTo": ["public.data", "public.content"],
        "UTTypeTagSpecification": {
            "public.filename-extension": ["mvpdf"],
            "public.mime-type": ["application/x-mvpdf"],
        },
    }]


def patch_info_plist(app: Path) -> None:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    updated = {
        **info,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleDocumentTypes": document_types(),
        "UTExportedTypeDeclarations": exported_types(),
        "LSUIElement": True,  # no Dock icon flash: the app only hands files to the browser
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(updated, handle)


def extension_id(public_key_b64: str) -> str:
    """Chrome's ID for an extension with this manifest "key": SHA-256, first 32 hex digits as a-p."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64)).hexdigest()[:32]
    return digest.translate(str.maketrans("0123456789abcdef", "abcdefghijklmnop"))


def host_launcher_source(python: str) -> str:
    log = Path.home() / "Library" / "Logs" / "MVPDF" / "native-host.log"
    return (
        "#!/bin/sh\n"
        f"mkdir -p {shlex.quote(str(log.parent))}\n"
        f'exec {shlex.quote(python)} {shlex.quote(str(NATIVE_HOST_SCRIPT))} "$@" 2>>{shlex.quote(str(log))}\n'
    )


def host_manifest(ext_id: str) -> dict:
    return {
        "name": NATIVE_HOST_NAME,
        "description": "MVPDF converter for the Save as MVPDF extension",
        "path": str(HOST_LAUNCHER),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }


def host_manifest_paths() -> list[Path]:
    browsers = [SUPPORT_DIR / name for name in BROWSER_DIRS]
    present = [d for i, d in enumerate(browsers) if i == 0 or d.is_dir()]
    return [d / "NativeMessagingHosts" / f"{NATIVE_HOST_NAME}.json" for d in present]


def install_native_host(python: str) -> str:
    key = json.loads((EXTENSION_DIR / "manifest.json").read_text())["key"]
    ext_id = extension_id(key)
    HOST_LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    HOST_LAUNCHER.write_text(host_launcher_source(python))
    HOST_LAUNCHER.chmod(0o755)
    manifest = json.dumps(host_manifest(ext_id), indent=2)
    for path in host_manifest_paths():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(manifest)
    return ext_id


def uninstall_native_host() -> None:
    for path in (*host_manifest_paths(), HOST_LAUNCHER):
        if path.exists():
            path.unlink()
            print(f"Removed {path}")


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def install(python: str) -> None:
    if sys.platform != "darwin":
        raise SystemExit("The double-click app is macOS-only. Use: python viewer/mvpdf_viewer.py open FILE")
    APP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "viewer.applescript"
        source.write_text(applet_source(python))
        built = Path(tmp) / f"{APP_NAME}.app"
        run("osacompile", "-o", str(built), str(source))
        patch_info_plist(built)
        run("codesign", "--force", "--deep", "--sign", "-", str(built))  # re-sign after plist edit
        if APP_PATH.exists():
            shutil.rmtree(APP_PATH)
        shutil.move(str(built), APP_PATH)
    run(str(LSREGISTER), "-f", str(APP_PATH))
    ext_id = install_native_host(python)
    print(f"Installed {APP_PATH}")
    print("Double-click any .mvpdf file to open it in a browser tab.")
    print("Right-click a PDF > Open With > MVPDF Viewer to convert and open it.")
    print(f"\nBrowser extension (ID {ext_id}): open chrome://extensions, turn on Developer mode,")
    print(f"click 'Load unpacked' and pick {EXTENSION_DIR}")


def uninstall() -> None:
    if APP_PATH.exists():
        subprocess.run([str(LSREGISTER), "-u", str(APP_PATH)], check=False, capture_output=True)
        shutil.rmtree(APP_PATH)
        print(f"Removed {APP_PATH}")
    uninstall_native_host()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        uninstall() if args.uninstall else install(sys.executable)
    except subprocess.CalledProcessError as err:
        print(f"error: {' '.join(err.cmd[:2])} failed: {err.stderr.strip()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
