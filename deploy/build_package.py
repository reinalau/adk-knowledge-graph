"""Build the AgentCore direct-code-deployment zip: python deploy/build_package.py

AgentCore Runtime only runs on Linux arm64, so dependencies are installed as manylinux aarch64 wheels
for Python 3.13 (the runtime set in Terraform). No Docker is needed.
Output: build/agentcore_package.zip (limits: 250 MB zipped, 750 MB unzipped).
"""
import configparser
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PACKAGE_DIR = BUILD / "package"
ZIP_PATH = BUILD / "agentcore_package.zip"

PYTHON_VERSION = "3.13"
PLATFORM = "manylinux2014_aarch64"
APP_SOURCES = ["agentcore_app.py", "kg_pipeline", "kg_pipeline_docs"]
EXCLUDED_DIRS = {"__pycache__", ".adk", ".pytest_cache"}
MAX_ZIPPED_MB = 250
MAX_UNZIPPED_MB = 750


def install_dependencies() -> None:
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--quiet",
            "--target", str(PACKAGE_DIR),
            "--platform", PLATFORM,
            "--implementation", "cp",
            "--python-version", PYTHON_VERSION,
            "--only-binary=:all:",
            "-r", str(ROOT / "deploy" / "requirements.txt"),
        ],
        check=True,
    )


def write_posix_launchers() -> list[str]:
    """Replace the console scripts pip generated for the build machine with Linux launchers.

    pip writes console scripts for the platform it runs on (on Windows, '.exe' files), even with
    --platform. AgentCore runs on Linux, and the 'opentelemetry-instrument' entry point needs a
    working launcher, so they are regenerated from each package's entry_points.txt.
    """
    bin_dir = PACKAGE_DIR / "bin"
    if bin_dir.exists():
        shutil.rmtree(bin_dir)
    bin_dir.mkdir()

    written = []
    for entry_points in PACKAGE_DIR.glob("*.dist-info/entry_points.txt"):
        parser = configparser.ConfigParser(delimiters=("=",))
        parser.optionxform = str  # keep script names as written
        parser.read(entry_points, encoding="utf-8")
        if not parser.has_section("console_scripts"):
            continue
        for name, target in parser.items("console_scripts"):
            module, _, attr = target.strip().partition(":")
            attr = attr.split()[0]  # drop optional "[extras]"
            script = (
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"from {module} import {attr.split('.')[0]}\n"
                f"sys.exit({attr}())\n"
            )
            (bin_dir / name).write_text(script, encoding="utf-8", newline="\n")
            written.append(name)
    return sorted(written)


def copy_app_sources() -> None:
    for name in APP_SOURCES:
        source = ROOT / name
        target = PACKAGE_DIR / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(*EXCLUDED_DIRS), dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)


def write_zip() -> tuple[float, float]:
    """Zip with explicit Unix permissions (644 files, 755 dirs and bin/ launchers): AgentCore needs
    them to read and run the code, and a zip created on Windows does not carry them."""
    unzipped = 0
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(PACKAGE_DIR.rglob("*")):
            if any(part in EXCLUDED_DIRS for part in path.relative_to(PACKAGE_DIR).parts):
                continue
            arcname = path.relative_to(PACKAGE_DIR).as_posix()
            if path.is_dir():
                info = zipfile.ZipInfo(arcname + "/")
                info.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(info, "")
            else:
                info = zipfile.ZipInfo.from_file(path, arcname)
                executable = arcname.startswith("bin/")
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(path, "rb") as fh:
                    zf.writestr(info, fh.read())
                unzipped += path.stat().st_size
    return ZIP_PATH.stat().st_size / 1024**2, unzipped / 1024**2


def main() -> None:
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    PACKAGE_DIR.mkdir(parents=True)
    ZIP_PATH.unlink(missing_ok=True)

    print(f"Installing dependencies for {PLATFORM} / Python {PYTHON_VERSION} ...")
    install_dependencies()
    launchers = write_posix_launchers()
    if "opentelemetry-instrument" not in launchers:
        sys.exit("opentelemetry-instrument launcher not found: is aws-opentelemetry-distro in deploy/requirements.txt?")
    copy_app_sources()
    zipped_mb, unzipped_mb = write_zip()
    print(f"Package: {ZIP_PATH} ({zipped_mb:.1f} MB zipped, {unzipped_mb:.1f} MB unzipped)")
    if zipped_mb > MAX_ZIPPED_MB or unzipped_mb > MAX_UNZIPPED_MB:
        sys.exit(f"Package exceeds the AgentCore limits ({MAX_ZIPPED_MB} MB zipped / {MAX_UNZIPPED_MB} MB unzipped)")


if __name__ == "__main__":
    main()
