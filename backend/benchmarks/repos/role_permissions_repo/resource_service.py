from models import Resource, User


def update_resource(user: User, resource: Resource, value: str) -> Resource:
    """Update a resource after authorization."""

    resource.value = value
    return resource
