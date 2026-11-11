import uuid


def generate_uuid() -> str:
    """Return a new UUID as a string."""
    return str(uuid.uuid4())
