"""HTTP pairing boundary; credentials are returned exactly once."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from gptlink.gateway.pairing import DeviceMetadata, PairingError, PairingService

router = APIRouter(prefix="/api/v1")


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=32, repr=False)
    metadata: DeviceMetadata


class PairingResponse(BaseModel):
    device_id: str
    token: str
    display_name: str


@router.post("/pair", response_model=PairingResponse, status_code=status.HTTP_201_CREATED)
async def redeem_pairing(request: Request, payload: PairingRequest) -> PairingResponse:
    try:
        credential = await PairingService(request.app.state.database).redeem(
            payload.code, payload.metadata
        )
    except PairingError as error:
        raise HTTPException(status_code=400, detail="invalid pairing request") from error
    return PairingResponse(
        device_id=str(credential.device_id),
        token=credential.token,
        display_name=payload.metadata.display_name,
    )
