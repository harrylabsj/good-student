"""Cold-start the bundled MCP without a global good-student installation.

Only stdlib is imported before creating the isolated environment. All setup
output goes to stderr so stdin/stdout remain exclusively for MCP messages.
"""

import hashlib
import os
import subprocess
import sys
import time
import venv
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def installation_lock(path: Path):
    with path.open("a+b") as handle:
        os.chmod(path, 0o600)
        if os.name == "nt":
            import msvcrt

            handle.write(b"0")
            handle.flush()

            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + 150
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("另一个 MCP 进程正在安装依赖，请稍后重新连接") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            release()


def runtime_environment():
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    return env


def prepare_runtime(package_root: Path) -> Path:
    if sys.version_info < (3, 10):  # noqa: UP036 - bootstrap also runs outside the project venv
        raise RuntimeError("本地 MCP 需要 Python 3.10 或更新版本")
    wheels = list((package_root / "pkg").glob("good_student-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("专家包缺少唯一的核心 wheel，请重新安装专家包")
    wheel = wheels[0]
    constraints = package_root / "runtime-constraints.txt"
    digest = hashlib.sha256(wheel.read_bytes() + constraints.read_bytes()).hexdigest()[:20]
    cache = Path(os.environ.get("GOOD_STUDENT_RUNTIME") or Path.home() / ".cache/good-student-mcp")
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(cache, 0o700)
    target = cache / f"{digest}-py{sys.version_info.major}{sys.version_info.minor}"
    interpreter = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = target / ".ready"
    with installation_lock(cache / "bootstrap.lock"):
        if not marker.is_file() or not interpreter.is_file():
            print("[学生错题本] 首次连接：准备独立 Python 环境与 MCP 依赖。", file=sys.stderr)
            venv.EnvBuilder(with_pip=True).create(target)
            subprocess.run(
                [str(interpreter), "-m", "pip", "install", "--no-input",
                 "--disable-pip-version-check", "--quiet", "--constraint", str(constraints),
                 f"{wheel}[mcp]"],
                check=True, env=runtime_environment(), stdout=sys.stderr, stderr=sys.stderr, timeout=120,
            )
            subprocess.run(
                [str(interpreter), "-c", "from good_student.mcp_server import mcp"],
                check=True, env=runtime_environment(), stdout=sys.stderr, stderr=sys.stderr, timeout=20,
            )
            marker.write_text(digest, encoding="ascii")
    return interpreter


def main():
    package_root = Path(__file__).resolve().parents[1]
    try:
        interpreter = prepare_runtime(package_root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[学生错题本] MCP 启动失败：{exc}；首次连接需要网络及 Python 3.10+。", file=sys.stderr)
        raise SystemExit(1) from exc
    os.execve(str(interpreter), [str(interpreter), "-m", "good_student.mcp_server"], runtime_environment())


if __name__ == "__main__":
    main()
