from fastapi import APIRouter

router = APIRouter()


@router.post("/llm")
async def save_llm_config():
    pass


@router.get("/llm")
async def get_llm_config():
    pass
