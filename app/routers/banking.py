import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app import bank_client, config, demo, dependencies, passi_auth
from app.services import bank_sync as bank_sync_service, banking as banking_service, sync_state


router = APIRouter()


def bank_authorization_flows(request: Request) -> dict[str, str]:
    stored = request.session.get("bank_auth_flows")
    flows = {
        str(state): str(binding)
        for state, binding in stored.items()
        if state and binding
    } if isinstance(stored, dict) else {}
    legacy = request.session.get("bank_auth_flow")
    if isinstance(legacy, dict) and legacy.get("state") and legacy.get("binding"):
        flows.setdefault(str(legacy["state"]), str(legacy["binding"]))
    return flows


async def consume_bank_authorization_state(request: Request, state: str) -> str:
    demo_owner = demo.owner_from_request(request)
    if state.startswith("demo_") or demo_owner:
        session = demo.store.get(demo_owner)
        expires_at = session.pending.pop(state, None)
        if expires_at is None or expires_at <= demo.now():
            raise HTTPException(400, "Invalid or expired demo authorization callback")
        return demo_owner
    binding = bank_authorization_flows(request).get(state, "") if passi_auth.AUTH_ENABLED else ""
    owner_id = await banking_service.consume_authorization(
        state, binding, require_binding=passi_auth.AUTH_ENABLED,
    )
    if passi_auth.AUTH_ENABLED:
        flows = bank_authorization_flows(request)
        flows.pop(state, None)
        if flows:
            request.session["bank_auth_flows"] = flows
        else:
            request.session.pop("bank_auth_flows", None)
        legacy = request.session.get("bank_auth_flow")
        if isinstance(legacy, dict) and legacy.get("state") == state:
            request.session.pop("bank_auth_flow", None)
    return owner_id


@router.get("/connect")
async def connect_to_swedbank(request: Request, owner_id: str = Depends(dependencies.current_owner_id)) -> RedirectResponse:
    headers = await bank_client.bank_api_headers(owner_id)
    state = secrets.token_urlsafe(32)
    demo_mode = owner_id.startswith(demo.PREFIX)
    if demo_mode:
        state = "demo_" + state
    browser_binding = secrets.token_urlsafe(32) if passi_auth.AUTH_ENABLED and not demo_mode else ""
    if browser_binding:
        flows = bank_authorization_flows(request)
        flows[state] = browser_binding
        while len(flows) > config.BANK_AUTH_BROWSER_FLOW_LIMIT:
            flows.pop(next(iter(flows)))
        request.session["bank_auth_flows"] = flows
        request.session.pop("bank_auth_flow", None)
    url = await banking_service.start_authorization(owner_id, state, browser_binding, headers)
    return RedirectResponse(url)


@router.post("/api/demo/connect")
async def connect_demo(request: Request, owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, str]:
    if not owner_id.startswith(demo.PREFIX):
        raise HTTPException(403, "Start a demo first")
    response = await connect_to_swedbank(request, owner_id)
    return {"url": response.headers["location"]}


@router.get("/callback")
async def authorization_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    if error:
        if state:
            await consume_bank_authorization_state(request, state)
        raise HTTPException(400, f"Bank authorization failed: {error}")
    if not code or not state:
        raise HTTPException(400, "Invalid or expired authorization callback")
    owner_id = await consume_bank_authorization_state(request, state)
    account_ids = await banking_service.connect_accounts(owner_id, code)
    background_tasks.add_task(bank_sync_service.sync_account_transactions, account_ids, owner_id)
    return RedirectResponse(
        url=f"/?bank_connected=1&accounts={len(account_ids)}&sync=pending",
        status_code=303,
    )


@router.post("/api/sync")
async def sync_transactions(owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, int]:
    result = await bank_sync_service.sync_owner_transactions(owner_id)
    await sync_state.record_auto_sync_result(owner_id, error=None)
    return result


@router.get("/api/sync")
async def sync_status(owner_id: str = Depends(dependencies.current_owner_id)) -> dict[str, str | None]:
    """Last successful bank synchronization for the authenticated owner, in UTC."""
    return await sync_state.get_sync_status(owner_id)
