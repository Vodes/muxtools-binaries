# ruff: noqa: F405 F403
from .meta import *
import platform
import zipfile


if "x86" in platform.machine().lower():
    env.update(OPT_FLAGS="-fno-strict-aliasing -O2 -march=x86-64-v3")


commands = ["chmod u+x build-linux.sh", "./build-linux.sh"]
for cmd in commands:
    run(cmd)

opus_zip = Path(f"opus-tools-{env['OPUS_VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip")
with zipfile.ZipFile(opus_zip, "w") as f:
    for bin in (Path(env["BUILD_DIR"]) / "bin").glob("*opus*"):
        f.write(bin, bin.name)

flac_zip = Path(f"flac-{env['FLAC_VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip")
with zipfile.ZipFile(flac_zip, "w") as f:
    for bin in (Path(env["BUILD_DIR"]) / "bin").glob("*flac*"):
        f.write(bin, bin.name)

# 4. Delete existing release for the tag (if exists)
for tag in ("opus-tools-latest", f"opus-tools-{env['OPUS_VERSION_NAME']}"):
    try:
        subprocess.run(f'gh release view "{tag}" --json id --jq .id', shell=True, check=True, capture_output=True, text=True)
        subprocess.run(f'gh release delete "{tag}" --yes', shell=True, check=True)
        print(f"Deleted old release for tag {tag}")
    except subprocess.CalledProcessError:
        print(f"No existing release found for tag {tag}")

    # 5. Create new release
    subprocess.run(f'gh release create "{tag}" --title "Release {tag}" --notes "Automated release for {tag}"', shell=True, check=True)

    # 6. Upload artifacts
    subprocess.run(f'gh release upload "{tag}" "{opus_zip}"', shell=True, check=True)

# 4. Delete existing release for the tag (if exists)
for tag in ("flac-latest", f"flac-{env['FLAC_VERSION_NAME']}"):
    try:
        subprocess.run(f'gh release view "{tag}" --json id --jq .id', shell=True, check=True, capture_output=True, text=True)
        subprocess.run(f'gh release delete "{tag}" --yes', shell=True, check=True)
        print(f"Deleted old release for tag {tag}")
    except subprocess.CalledProcessError:
        print(f"No existing release found for tag {tag}")

    # 5. Create new release
    subprocess.run(f'gh release create "{tag}" --title "Release {tag}" --notes "Automated release for {tag}"', shell=True, check=True)

    # 6. Upload artifacts
    subprocess.run(f'gh release upload "{tag}" "{flac_zip}"', shell=True, check=True)
