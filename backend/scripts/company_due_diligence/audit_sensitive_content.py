"""审计企业尽调代码、测试和文档中的连接凭据泄漏。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent if (BACKEND_ROOT.parent / "backend").is_dir() else BACKEND_ROOT
SCAN_ROOTS = (
    PROJECT_ROOT / ".env.template",
    BACKEND_ROOT / "package/yuxi/company_due_diligence",
    BACKEND_ROOT / "scripts/company_due_diligence",
    BACKEND_ROOT / "test/unit/company_due_diligence",
    BACKEND_ROOT / "test/integration/company_due_diligence",
    BACKEND_ROOT / "test/e2e/company_due_diligence",
    PROJECT_ROOT / "web/src/components/company-due-diligence",
    PROJECT_ROOT / "docs/agents/company-due-diligence-system.md",
    PROJECT_ROOT / "docs/agents/company-due-diligence-implementation-guide.md",
    PROJECT_ROOT / "docs/agents/company-due-diligence-end-to-end-script.md",
    PROJECT_ROOT / "tasks/company-due-diligence",
)
MYSQL_URI = re.compile(r"mysql\+aiomysql://([^\s:@/]+):([^\s@/]+)@")
PLACEHOLDER_PASSWORD_MARKERS = ("replace-with-password", "password", "${")


def main() -> None:
    """扫描显式 MySQL 凭据，并检查源码工作区或生产镜像中的 .env。"""
    violations = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in MYSQL_URI.finditer(line):
                password = match.group(2)
                is_placeholder = "mysql.example.com" in line or any(
                    marker in password for marker in PLACEHOLDER_PASSWORD_MARKERS
                )
                if not is_placeholder:
                    relative_path = path.relative_to(PROJECT_ROOT)
                    violations.append(f"{relative_path}:{line_number} 包含非占位 MySQL URI")

    if (PROJECT_ROOT / ".git").exists():
        tracked_env = (
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", ".env"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        ignored_env = (
            subprocess.run(
                ["git", "check-ignore", "-q", ".env"],
                cwd=PROJECT_ROOT,
                check=False,
            ).returncode
            == 0
        )
        if tracked_env:
            violations.append(".env 被 Git 跟踪")
        if not ignored_env:
            violations.append(".env 未被 .gitignore 忽略")
        environment_result = ".env 未跟踪且已忽略"
    else:
        env_path = PROJECT_ROOT / ".env"
        if env_path.exists() and not _is_mounted_file(env_path):
            violations.append("生产镜像包含 .env 文件")
        environment_result = ".env 仅作为开发挂载注入" if env_path.exists() else "生产镜像未包含 .env 文件"

    if violations:
        raise SystemExit("敏感信息审计失败:\n- " + "\n- ".join(violations))
    print(f"敏感信息审计通过：未发现非占位 MySQL URI，{environment_result}。")


def _iter_files():
    """遍历企业尽调相关的可审计文本文件。"""
    for root in SCAN_ROOTS:
        if root.is_file():
            yield root
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix not in {".pyc", ".png", ".jpg", ".sqlite"}:
                yield path


def _is_mounted_file(path: Path) -> bool:
    """判断文件是否作为独立挂载点注入当前容器。"""
    mount_info = Path("/proc/self/mountinfo")
    if not mount_info.exists():
        return False
    target = f" {path.resolve()} "
    return any(target in line for line in mount_info.read_text(encoding="utf-8").splitlines())


if __name__ == "__main__":
    main()
