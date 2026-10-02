"""
4.10 — KB namespace: пути из search_knowledge_base должны быть пригодны для read_file.

Проверяем маппинг путей (без обращения к векторной БД): metadata.file_path
относителен корня источника, а агенту нужен путь относительно PROJECT_ROOT.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from mcp_servers import contextus_rag as rag


def test_file_path_is_relative_to_project_root():
    root = str(rag.PROJECT_ROOT)
    assert rag._agent_path("main.py", f"{root}/backend") == "backend/main.py"
    assert rag._agent_path("backend/main.py", root) == "backend/main.py"
    assert rag._agent_path("backend/config/models.yaml", root) == "backend/config/models.yaml"


def test_absolute_file_path_is_mapped():
    root = rag.PROJECT_ROOT
    assert rag._agent_path(str(root / "backend" / "main.py"), str(root)) == "backend/main.py"


def test_outside_source_is_not_readable():
    """Файлы вне рабочей папки агента помечаются как недоступные для read_file."""
    assert rag._agent_path("passwd", "/etc") is None
    assert rag._agent_path("/etc/hostname", "") is None


def test_host_mount_of_same_tree_is_mapped():
    """Источник, проиндексированный по host-пути того же дерева, тоже даёт читаемый путь."""
    host_root = "/home/ai-line/Projects/dsm"
    if not Path(host_root).is_dir() or not rag._same_tree(Path(host_root), rag.PROJECT_ROOT):
        import pytest
        pytest.skip("host-путь проекта недоступен в этом окружении")
    assert rag._agent_path("backend/main.py", host_root) == "backend/main.py"


def test_same_tree_detects_different_dirs(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert rag._same_tree(other, rag.PROJECT_ROOT) is False
    assert rag._same_tree(rag.PROJECT_ROOT, rag.PROJECT_ROOT) is True
