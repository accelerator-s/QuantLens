from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
import yaml

from exceptions import LifecycleError
from paths import workspace

class ChampionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_shadow_observations: int = Field(ge=1, le=252)
    shadow_observation_days: int = Field(ge=1, le=252)
    min_challenger_net_excess_return: float = Field(ge=-1, le=1)
    max_cost_rate_increase: float = Field(ge=0, le=1)
    max_drawdown_worsening: float = Field(ge=0, le=1)
    max_turnover_increase: float = Field(ge=0)

def load_champion_policy() -> ChampionPolicy:
    path = workspace().config / "champion_policy.yaml"
    if not path.is_file():
        raise LifecycleError(
            code="CHAMPION_POLICY_MISSING",
            message="缺少 champion/challenger 治理阈值配置。",
            blocking=True,
            suggested_action="创建并人工审阅 config/champion_policy.yaml。",
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return ChampionPolicy.model_validate(raw)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise LifecycleError(
            code="CHAMPION_POLICY_INVALID",
            message=f"champion/challenger 治理阈值无效：{exc}",
            blocking=True,
            suggested_action="修正治理配置，不得静默使用默认晋升阈值。",
        ) from exc

__all__ = ["ChampionPolicy", "load_champion_policy"]
