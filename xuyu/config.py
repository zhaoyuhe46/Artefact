"""模型、温度、阈值、版本号。改这里不需要动其他代码。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    """读取 .env（KEY=VALUE），不覆盖已有环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DATABASE_URL = os.getenv("DATABASE_URL", "")

GEN_MODEL = os.getenv("MODEL_GEN", "deepseek/deepseek-v4.1-flash-1017")      # 固定快照，结果可复现
JUDGE_MODEL = os.getenv("MODEL_JUDGE", "anthropic/claude-sonnet-5.5")          # 不同厂商打分，避免自评
EMBED_MODEL = os.getenv("MODEL_EMBED", "openai/text-embedding-3-small")
EMBED_DIM = 1536

TEMPERATURE = {"generate": 1.0, "repair": 0.8, "judge": 0.0}
MAX_TOKENS = {"generate": 2000, "repair": 1500, "judge": 2500}
RAG_K = 5
HISTORY_N = 8
SIM_THRESHOLD = 0.5             # 三条之间字符二元组 Jaccard 上限
BEREAVEMENT_QUIET_DAYS = 30
WAITING_DAYS = 3                # 客户消息在几天内算「在等回复」（v2）
TARGET_LEN = (30, 70)           # 提示里的目标字数，硬上限仍是 80（v2）
MARKETING_SCENARIOS = {"recommendation", "reactivation", "event"}
COMPLAINT_DAYS = 60
COMPLAINT_OVERRIDE_SCENARIOS = {"recommendation", "reactivation", "event", "post_purchase"}   # v2
VERSION_LABEL = os.getenv("ENGINE_VERSION", "v3")
