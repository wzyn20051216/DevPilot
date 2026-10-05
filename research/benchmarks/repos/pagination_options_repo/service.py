from schemas import PageOptions


def list_users(options: PageOptions | None = None) -> tuple[int, int]:
    """Return the database offset and limit for a user query."""

    current = options or PageOptions()
    page_size = current.page_size or 10
    return current.page * page_size, page_size
