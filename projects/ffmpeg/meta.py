from pathlib import Path
import os

cwd = Path(__file__).parent

meta_env = dict(
    # Release
    VERSION_NAME="2025-12-27",
    VERSION_CODE="1",
)

env = os.environ.copy()
env.update(**meta_env)

if os.name == "nt":
    out_dir = Path(R"C:\tmp\builds")
else:
    out_dir = Path("/tmp/builds")

out_dir.mkdir(exist_ok=True, parents=True)


WIN_X86_URL = "https://github.com/Vodes/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.0-latest-win64-nonfree-8.0.zip"
LINUX_X86_URL = "https://github.com/Vodes/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.0-latest-linux64-nonfree-8.0.tar.xz"
