"""引擎内部传递的数据结构。"""
from typing import List, Optional

from pydantic import BaseModel, Field

SCENARIOS = {
    "birthday": "生日与节日祝福",
    "recommendation": "产品推荐",
    "post_purchase": "购后关怀",
    "reactivation": "沉睡客户唤醒",
    "event": "活动邀约",
    "price_objection": "价格异议",
}


class Check(BaseModel):
    rule: str
    ok: bool
    detail: str = ""


class Candidate(BaseModel):
    strategy_id: str
    strategy_name: str
    text: str
    rationale: str = ""
    refs: List[str] = Field(default_factory=list)
    checks: List[Check] = Field(default_factory=list)
    repaired: bool = False
    previous_text: Optional[str] = None

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)
