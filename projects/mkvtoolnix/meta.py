from pathlib import Path
import os

cwd = Path(__file__).parent

meta_env = dict(
    # Release
    VERSION_NAME="96.0",
    VERSION_CODE="1",
)

env = os.environ.copy()
env.update(**meta_env)

if os.name == "nt":
    out_dir = Path(R"C:\tmp\builds")
else:
    out_dir = Path("/tmp/builds")

out_dir.mkdir(exist_ok=True, parents=True)


WIN_X86_URL = f"https://mkvtoolnix.download/windows/releases/{meta_env['VERSION_NAME']}/mkvtoolnix-64-bit-{meta_env['VERSION_NAME']}.7z"
LINUX_X86_URL = f"https://mkvtoolnix.download/appimage/MKVToolNix_GUI-{meta_env['VERSION_NAME']}-x86_64.AppImage"
