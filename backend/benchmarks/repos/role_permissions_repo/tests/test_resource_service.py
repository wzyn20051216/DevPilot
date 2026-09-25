import pytest

from models import Resource, User
from policy import can_edit
from resource_service import update_resource


def test_owner_can_edit() -> None:
    user = User(id="owner", role="member")
    resource = Resource(owner_id="owner", value="old")
    assert update_resource(user, resource, "new").value == "new"


def test_admin_can_edit_other_users_resource() -> None:
    admin = User(id="admin", role="admin")
    resource = Resource(owner_id="owner", value="old")
    assert can_edit(admin, resource) is True
    assert update_resource(admin, resource, "new").value == "new"


def test_member_cannot_edit_other_users_resource() -> None:
    member = User(id="member", role="member")
    resource = Resource(owner_id="owner", value="old")
    assert can_edit(member, resource) is False
    with pytest.raises(PermissionError):
        update_resource(member, resource, "new")
    assert resource.value == "old"
