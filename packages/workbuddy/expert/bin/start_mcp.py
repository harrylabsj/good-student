"""Cold-start the bundled MCP without a global good-student installation.

Only stdlib is imported before preparing the runtime. Setup messages go to
stderr so stdin/stdout remain exclusively for MCP messages.
"""

import hashlib
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
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


def runtime_target():
    return [platform.system(), platform.machine(), f"{sys.version_info.major}.{sys.version_info.minor}"]


def prefer_bundled_interpreter(targets: list[dict]) -> None:
    """Find an installed Python matching the bundled wheels, including from a GUI PATH."""
    current = runtime_target()
    supported = [entry["target"] for entry in targets]
    if current in supported:
        return

    names = [f"python{target[2]}" for target in supported if target[:2] == current[:2]]
    if os.name == "nt":
        return
    search_path = os.pathsep.join(filter(None, [os.environ.get("PATH"), "/opt/homebrew/bin", "/usr/local/bin"]))
    checked = {Path(sys.executable).resolve()}
    for name in [*names, "python3"]:
        for directory in search_path.split(os.pathsep):
            if not Path(directory).is_absolute():
                continue
            located = shutil.which(name, path=directory)
            if not located:
                continue
            candidate = Path(located).resolve()
            if candidate in checked:
                continue
            checked.add(candidate)
            try:
                probe = subprocess.run(
                    [str(candidate), "-I", "-c",
                     "import json, platform, sys; print(json.dumps([platform.system(), platform.machine(), "
                     "f'{sys.version_info.major}.{sys.version_info.minor}']))"],
                    check=True, capture_output=True, text=True, env=runtime_environment(), timeout=3,
                )
                target = json.loads(probe.stdout)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            if target in supported:
                os.execve(str(candidate), [str(candidate), "-I", str(Path(__file__).resolve())], runtime_environment())


def unpack_offline_runtime(wheels: list[Path], destination: Path) -> None:
    # The build verifies that these wheels install entirely into site-packages.
    # Stage under the same parent so publishing the completed cache is atomic.
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        site = Path(temporary) / "site"
        site.mkdir()
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.namelist():
                    root = member.split("/", 1)[0]
                    if root.endswith(".data") or member.endswith(".pth"):
                        raise ValueError(f"离线 wheel 包含不支持的安装文件：{wheel.name}: {member}")
                archive.extractall(site)
        (site / ".ready").touch()
        if destination.exists():
            shutil.rmtree(destination)
        site.rename(destination)


def prepare_runtime(package_root: Path) -> tuple[Path, bool]:
    wheels = list((package_root / "pkg").glob("good_student-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("专家包缺少唯一的核心 wheel，请重新安装专家包")
    wheel = wheels[0]
    constraints = package_root / "runtime-constraints.txt"
    wheelhouse = package_root / "wheelhouse"
    targets_path = package_root / "offline-targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    prefer_bundled_interpreter(targets)
    if sys.version_info < (3, 10):  # noqa: UP036 - the first Python can be the macOS system Python 3.9
        raise RuntimeError("未找到与包内依赖匹配的 Python；本地 MCP 需要 Python 3.10 或更新版本")
    target_platform = runtime_target()
    offline_names = next((entry["wheels"] for entry in targets if entry["target"] == target_platform), None)
    offline_wheels = [wheel, *(wheelhouse / name for name in offline_names)] if offline_names is not None else None

    fingerprint = hashlib.sha256()
    artifacts = [constraints, targets_path, *(offline_wheels or [wheel])]
    for artifact in artifacts:
        fingerprint.update(artifact.name.encode("utf-8"))
        fingerprint.update(artifact.read_bytes())
    digest = fingerprint.hexdigest()[:20]
    cache = Path(os.environ.get("GOOD_STUDENT_RUNTIME") or Path.home() / ".cache/good-student-mcp")
    cache.mkdir(parents=True, exist_ok=True)
    os.chmod(cache, 0o700)
    target = cache / f"{digest}-py{sys.version_info.major}{sys.version_info.minor}"
    if offline_wheels is not None:
        with installation_lock(cache / "bootstrap.lock"):
            if not (target / ".ready").is_file():
                unpack_offline_runtime(offline_wheels, target)
        return target, True

    import venv

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
    return interpreter, False


def main():
    if not sys.flags.isolated:
        executable = sys.executable
        os.execve(executable, [executable, "-I", str(Path(__file__).resolve())], runtime_environment())
    package_root = Path(__file__).resolve().parents[1]
    try:
        runtime, offline = prepare_runtime(package_root)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"[学生错题本] MCP 启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if offline:
        sys.path.insert(0, str(runtime))
        runpy.run_module("good_student.mcp_server", run_name="__main__", alter_sys=True)
    else:
        os.execve(str(runtime), [str(runtime), "-m", "good_student.mcp_server"], runtime_environment())


if __name__ == "__main__":
    main()
