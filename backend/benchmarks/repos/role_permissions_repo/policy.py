from models import Resource, User


def can_edit(user: User, resource: Resource) -> bool:
    """Return whether the user may edit this resource."""

    return bool(user.id)
