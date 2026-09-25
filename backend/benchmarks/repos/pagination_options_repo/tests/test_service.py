import pytest

from config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from schemas import PageOptions
from service import list_users


def test_default_options_remain_backward_compatible() -> None:
    assert list_users() == (0, DEFAULT_PAGE_SIZE)


def test_page_is_one_based() -> None:
    assert list_users(PageOptions(page=3, page_size=25)) == (50, 25)


@pytest.mark.parametrize(
    "options",
    [
        PageOptions(page=0, page_size=20),
        PageOptions(page=1, page_size=0),
        PageOptions(page=1, page_size=MAX_PAGE_SIZE + 1),
    ],
)
def test_invalid_options_are_rejected(options: PageOptions) -> None:
    with pytest.raises(ValueError):
        list_users(options)
