from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from yuxi.agents.skills import service as svc

INSTALLER_SCRIPT = textwrap.dedent(
    """
    import sys
    import time
    from pathlib import Path

    from yuxi.agents.skills import service as svc

    save_dir, source_dir, worker_id = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    svc.sys_config.save_dir = str(save_dir)
    (save_dir / f"ready-{worker_id}").touch()
    while not (save_dir / "go").exists():
        time.sleep(0.01)
    for _ in range(100):
        svc._install_builtin_skill_dir(svc.get_skills_root_dir() / "image-gen", source_dir)
    """
)


def test_concurrent_builtin_skill_installs_on_shared_save_dir_do_not_crash(tmp_path: Path):
    """api 与 worker 共享 saves 目录并在启动时同时安装内置 Skill，不能因目录替换竞争而崩溃。"""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    source_dir = tmp_path / "builtin" / "image-gen"
    (source_dir / "scripts").mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("---\nname: image-gen\ndescription: x\n---\n", encoding="utf-8")
    (source_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")

    workers = [
        subprocess.Popen(
            [sys.executable, "-c", INSTALLER_SCRIPT, str(save_dir), str(source_dir), str(worker_id)],
            stderr=subprocess.PIPE,
            text=True,
        )
        for worker_id in range(2)
    ]
    while not all((save_dir / f"ready-{worker_id}").exists() for worker_id in range(2)):
        assert all(worker.poll() is None for worker in workers), [w.stderr.read() for w in workers]
        time.sleep(0.01)
    (save_dir / "go").touch()

    errors = [worker.communicate(timeout=60)[1].strip().splitlines()[-1:] for worker in workers]
    assert [worker.returncode for worker in workers] == [0, 0], errors
    installed = save_dir / "skills" / "image-gen"
    assert (installed / "scripts" / "run.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert sorted(path.name for path in installed.parent.iterdir()) == ["image-gen"]


LOCK_HOLDER_SCRIPT = textwrap.dedent(
    """
    import fcntl
    import sys
    import time
    from pathlib import Path

    lock_path, ready = Path(sys.argv[1]), Path(sys.argv[2])
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        ready.touch()
        time.sleep(0.6)
    """
)


@pytest.mark.asyncio
async def test_waiting_for_install_lock_does_not_block_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """另一个进程持有安装锁时，init_builtin_skills 在线程里等待，事件循环上的其他任务照常推进。"""
    monkeypatch.setattr(svc.sys_config, "save_dir", str(tmp_path))
    source_dir = tmp_path / "builtin" / "reporter"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("---\nname: reporter\ndescription: x\n---\n", encoding="utf-8")
    spec = SimpleNamespace(
        slug="reporter",
        source_dir=source_dir,
        description="x",
        tool_dependencies=(),
        mcp_dependencies=(),
        skill_dependencies=(),
    )
    monkeypatch.setattr(svc, "get_builtin_skill_specs", lambda: [spec])

    class FakeRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            return None

        async def create(self, **kwargs):
            return SimpleNamespace(**kwargs)

    monkeypatch.setattr(svc, "SkillRepository", FakeRepo)
    ready = tmp_path / "holder-ready"
    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER_SCRIPT, str(tmp_path / ".builtin-skills.lock"), str(ready)]
    )
    while not ready.exists():
        await asyncio.sleep(0.01)

    install = asyncio.create_task(svc.init_builtin_skills(None))
    started = time.monotonic()
    await asyncio.sleep(0.05)
    tick_delay = time.monotonic() - started

    assert tick_delay < 0.3, f"持锁等待阻塞了事件循环 {tick_delay:.2f}s"
    assert not install.done()
    assert [item.slug for item in await install] == ["reporter"]
    assert holder.wait(timeout=10) == 0
