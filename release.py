from pathlib import Path
import zipfile
import json
import shutil
from utils import run_cmd, ToolsData, Version

with open("versions.json", "r", encoding="utf-8") as f:
    version_data = ToolsData(tools=json.load(f))

for tool in Path("/tmp/builds").iterdir():
    if not tool.is_dir():
        continue

    zips = [build for build in tool.iterdir() if build.suffix == ".zip"]
    if not zips:
        print(f"No zip found for {tool}")
        continue

    with zipfile.ZipFile(zips[0], "r") as f:
        with f.open(".metadata.json", "r") as json_file:
            data = json.load(json_file)
            name = data["NAME"]
            version = data["VERSION"]
            version_code = str(data.get("VERSION_CODE", "0"))

    tags = [f"latest-{name}", f"{name}-{version}"]

    for tag in tags:
        print(f"\nProcessing tag: {tag}")

        # Delete old release if exists
        run_cmd(f'gh release view "{tag}" --json id --jq .id', check=False)
        run_cmd(f'gh release delete "{tag}" --yes', check=False)

        # Delete old tag locally and remotely
        run_cmd(f'git tag -d "{tag}"', check=False)
        run_cmd(f"git push origin :refs/tags/{tag}", check=False)

        # Create new tag and push
        run_cmd(f'git tag "{tag}"')
        run_cmd(f'git push origin "{tag}"')

        # Create new release
        run_cmd(f'gh release create "{tag}" --title "Release {tag}" --notes "Automated release for {tag}"')

        # Upload all zips for this tool
        if tag.startswith("latest"):
            latest_zips = []
            for zip in zips:
                latest_zip = zip.parent / zip.name.replace(version, "latest")
                shutil.copy(zip, latest_zip)
                latest_zips.append(latest_zip)
            for artifact in latest_zips:
                run_cmd(f'gh release upload "{tag}" "{artifact}"')
        else:
            for artifact in zips:
                run_cmd(f'gh release upload "{tag}" "{artifact}"')

    tool = version_data.tools[name]

    if any(v.version == version for v in tool.versions):
        print(f"Version {version} already exists for {name} in versions.json")
    else:
        tool.versions.append(Version(version=version, version_code=version_code))
        print(f"Added {version} ({version_code}) to {name} in versions.json")
        Path("versions.json").write_text(json.dumps({k: v.model_dump() for k, v in version_data.tools.items()}, indent=4), encoding="utf-8")
        run_cmd("git config user.name github-actions")
        run_cmd("git config user.email github-actions@github.com")
        run_cmd('git add "versions.json"')
        run_cmd('git commit -m "Add new versions to versions.json"', check=False)
        run_cmd("git push")
