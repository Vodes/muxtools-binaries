from pathlib import Path
import os

cwd = Path(__file__).parent

meta_env = dict(
    # Release
    VERSION_NAME="2025-08-05",
    VERSION_CODE="0",
)

env = os.environ.copy()
env.update(**meta_env)

if os.name == "nt":
    out_dir = Path(R"C:\tmp\builds")
else:
    out_dir = Path("/tmp/builds")

out_dir.mkdir(exist_ok=True, parents=True)


WIN_X86_URL = "https://github.com/Vodes/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-nonfree-7.1.zip"
LINUX_X86_URL = "https://github.com/Vodes/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-linux64-nonfree-7.1.tar.xz"
