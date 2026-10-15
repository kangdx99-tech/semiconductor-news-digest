"""配置加载：.env + config.yaml。

设计原则：
1. 不依赖 python-dotenv（自己 parse，15 行）；
2. config.yaml 里的值优先于 .env；
3. 所有密钥只能通过环境变量 / .env 提供，绝不写进 yaml，避免误提交到 GitHub。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------- .env 解析
def load_dotenv(path: str | Path, override: bool = False) -> int:
    """极简 .env 解析器。返回成功读入的键数量。

    支持 `KEY=value`、`export KEY=value`、引号包裹、`#` 注释。
    """
    p = Path(path)
    if not p.exists():
        return 0
    count = 0
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split(" #")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


# ---------------------------------------------------------------- 默认配置
DEFAULT_CONFIG: Dict[str, Any] = {
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "temperature": 0.4,
        "max_tokens": 4000,
        "timeout": 180,
    },
    "digest": {
        "title": "半导体新闻日报",
        "window_hours": 24,
        "monday_window_hours": 72,
        "max_items": 12,
        "timezone": "Asia/Shanghai",
    },
    "collect": {
        "timeout": 20,
        "max_per_source": 15,
    },
    "verify": {
        "enabled": True,
        "timeout": 15,
        "workers": 12,
        "accept_403": True,
    },
    "deliver": [{"type": "local_md", "dir": "./output"}],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class ConfigError(RuntimeError):
    pass


class Config:
    """点号取值的配置对象：cfg.get("llm.model")。"""

    def __init__(self, data: Dict[str, Any], base_dir: Path):
        self.data = data
        self.base_dir = base_dir

    # ---------------------------------------------------------- 构造
    @classmethod
    def load(
        cls,
        config_path: Optional[str | Path] = None,
        env_path: Optional[str | Path] = None,
        base_dir: Optional[str | Path] = None,
    ) -> "Config":
        base = Path(base_dir).resolve() if base_dir else Path.cwd().resolve()

        # .env: 先找显式路径，再找 base_dir/.env，最后找脚本同级目录
        candidates = []
        if env_path:
            candidates.append(Path(env_path))
        candidates += [base / ".env", Path(__file__).resolve().parent.parent / ".env"]
        for candidate in candidates:
            if candidate.exists():
                load_dotenv(candidate)
                break

        data = dict(DEFAULT_CONFIG)
        cfg_file = Path(config_path) if config_path else base / "config.yaml"
        if cfg_file.exists():
            if yaml is None:
                raise ConfigError("需要 PyYAML：pip install pyyaml")
            user_cfg = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
            if not isinstance(user_cfg, dict):
                raise ConfigError(f"{cfg_file} 顶层必须是映射（key: value）")
            data = _deep_merge(data, user_cfg)
        return cls(data, base)

    # ---------------------------------------------------------- 取值
    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, path: str) -> Any:
        value = self.get(path)
        if value in (None, "", [], {}):
            raise ConfigError(f"缺少必需配置项：{path}")
        return value

    def env(self, name: str, required: bool = True) -> str:
        value = os.environ.get(name, "").strip()
        if not value and required:
            raise ConfigError(
                f"环境变量 {name} 未设置。请在 .env 中填写（参考 .env.example）。"
            )
        return value

    def flag(self, path: str) -> bool:
        value = self.get(path, False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def sources(self) -> List[Dict[str, Any]]:
        return list(self.get("sources", []) or [])

    def searches(self) -> List[Dict[str, Any]]:
        return list(self.get("searches", []) or [])

    def deliveries(self) -> List[Dict[str, Any]]:
        items = self.get("deliver") or []
        return [dict(item) for item in items if isinstance(item, dict)]

    def resolve_path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.base_dir / p)
