from dataclasses import dataclass


@dataclass
class PageOptions:
    page: int = 0
    page_size: int | None = None
