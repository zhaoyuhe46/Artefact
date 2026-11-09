"""读取 skills/<mode>/SKILL.md：YAML 头声明检索项与策略名，正文是写作指引，原样放进系统提示。"""
from functools import lru_cache

import yaml

from .config import ROOT


@lru_cache(maxsize=None)
def load(mode: str) -> dict:
    raw = (ROOT / "skills" / mode / "SKILL.md").read_text(encoding="utf-8")
    _, head, body = raw.split("---", 2)
    meta = yaml.safe_load(head)
    meta["body"] = body.strip()
    meta.setdefault("rag", {"tags": [], "k": 5})
    meta.setdefault("facts", [])
    return meta
