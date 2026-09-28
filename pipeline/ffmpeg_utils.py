"""ffmpeg binary lookup — uses the free static binary bundled by imageio-ffmpeg
(pip-installed, no Homebrew/sudo needed) unless FFMPEG_BINARY env var overrides it."""
import os
import subprocess

_FFMPEG_PATH = None


def get_ffmpeg_path():
    global _FFMPEG_PATH
    if _FFMPEG_PATH:
        return _FFMPEG_PATH
    override = os.environ.get("FFMPEG_BINARY")
    if override:
        _FFMPEG_PATH = override
        return _FFMPEG_PATH
    import imageio_ffmpeg
    _FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
    return _FFMPEG_PATH


def run_ffmpeg(args, **kwargs):
    cmd = [get_ffmpeg_path()] + args
    return subprocess.run(cmd, **kwargs)
