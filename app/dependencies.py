from fastapi import HTTPException, Request


def current_owner_id(request: Request) -> str:
    owner_id = getattr(request.state, "owner_id", "")
    if not owner_id:
        raise HTTPException(401, "User identity is unavailable")
    return owner_id
