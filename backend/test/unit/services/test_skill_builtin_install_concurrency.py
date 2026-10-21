from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

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
