from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: str
    role: str


@dataclass
class Resource:
    owner_id: str
    value: str
