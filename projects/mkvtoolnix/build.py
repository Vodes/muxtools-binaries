# ruff: noqa: F405 F403
from meta import *
from utils import check_version_exists, run_cmd, download_file
import platform
import zipfile
import json
import shutil

if check_version_exists("mkvtoolnix", env["VERSION_NAME"]):
    print("Skipping mkvtoolnix builds! (Versions already exist)")
    exit(0)

out_zip = out_dir / "mkvtoolnix" / f"mkvtoolnix-{env['VERSION_NAME']}-{platform.system().lower()}-{platform.machine().lower()}.zip"
json_metadata = json.dumps(dict(NAME="mkvtoolnix", VERSION=env["VERSION_NAME"], VERSION_CODE=env["VERSION_CODE"]))

out_zip.parent.mkdir(exist_ok=True, parents=True)

if os.name == "nt":
    downloaded = Path("mkvtoolnix.zip")
    download_file(WIN_X86_URL, downloaded)
    zip_dir = out_dir / "extracted"
    run_cmd(f'7z -o"{zip_dir.resolve()}" x "{downloaded.resolve()}"', cwd=cwd)

    main_dir = next(zip_dir.rglob("mkv*.exe")).parent
    (main_dir / ".metadata.json").write_text(json_metadata, "utf-8")

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as f:
        all_files = list(main_dir.rglob("*"))
        common_path = os.path.commonpath(all_files)
        for file in filter(lambda x: x.is_file() and not x.parent.name == "examples", all_files):
            f.write(file, os.path.relpath(file, common_path))

    shutil.rmtree(zip_dir, True)
else:
    downloaded = cwd / "mkvtoolnix.AppImage"
    download_file(LINUX_X86_URL, downloaded)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as f:
        f.write(downloaded, downloaded.name)
        symlinks = {"mkvmerge", "mkvpropedit", "mkvinfo", "mkvextract"}
        for link in symlinks:
            with open(cwd / link, "w", encoding="utf-8") as link_file:
                link_file.writelines(["#!/bin/bash\n", "chmod u+x mkvtoolnix.AppImage\n", f"ln -sf mkvtoolnix.AppImage {link}\n", f'./{link} "$@"'])
            f.write(cwd / link, link)

        f.writestr(".metadata.json", json_metadata)
