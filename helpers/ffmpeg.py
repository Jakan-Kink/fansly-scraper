"""FFmpeg Launcher Module"""

import platform
import shutil
import subprocess


def get_ffmpeg_bin() -> str:
    ffmpeg_bin = None

    if platform.system() == "Linux":
        ffmpeg_bin = shutil.which("ffmpeg")

    if ffmpeg_bin is None:
        from pyffmpeg import FFmpeg

        ffmpeg = FFmpeg(enable_log=False)
        ffmpeg_bin = ffmpeg.get_ffmpeg_bin()

    return ffmpeg_bin


def run_ffmpeg(args: list[str]) -> bool:
    from textio import print_debug

    proc_args = [get_ffmpeg_bin()]
    proc_args += args

    print_debug(f"Running ffmpeg command: {' '.join(proc_args)}")
    try:
        result = subprocess.run(
            proc_args,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        print_debug(f"ffmpeg stdout: {result.stdout}")
        if result.stderr:
            print_debug(f"ffmpeg stderr: {result.stderr}")
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print_debug(f"ffmpeg failed with return code {e.returncode}")
        print_debug(f"ffmpeg stdout: {e.stdout}")
        print_debug(f"ffmpeg stderr: {e.stderr}")
        raise
