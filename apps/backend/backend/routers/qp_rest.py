from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel

router = APIRouter(prefix="/qp")


class StoreReq(BaseModel):
    value: str


def get_db(request: Request):
    return request.app.state.db  # lifespan opened it


def _decode_hex_key(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:  # pragma: no cover - trivial branch
        raise HTTPException(status_code=422, detail="Key must be valid hex") from exc
    if len(key) != 16:
        raise HTTPException(status_code=422, detail="Key must be 16 bytes")
    return key


@router.post("/{key_hex}")
def store(key_hex: str, payload: StoreReq, db=Depends(get_db)):
    key = _decode_hex_key(key_hex)
    db[key] = payload.value.encode()
    return {"status": "ok"}


@router.get("/{key_hex}")
def retrieve(key_hex: str, db=Depends(get_db)):
    key = _decode_hex_key(key_hex)
    val = db.get(key)
    if val is None:
        raise HTTPException(status_code=404, detail="Not found")
    return {"key": key_hex, "value": val.decode()}
