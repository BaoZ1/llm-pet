import yaml
from pathlib import Path
from dataclasses import dataclass


yaml.add_multi_representer(
    Path,
    lambda d, v: d.represent_scalar("!path", str(v.absolute())),
)
yaml.add_constructor(
    "!path",
    lambda l, n: Path(l.construct_scalar(n)),
)


@dataclass
class BaseConfig:
    enabled: bool
