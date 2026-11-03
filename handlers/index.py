from fastapi import APIRouter, Depends
import aiosqlite

from db import get_db, DB
from errors import ErrorCodes
from lib.response import StandardResponse
from model.item import Item

router = APIRouter()

@router.get("/")
async def root(conn: aiosqlite.Connection = Depends(get_db)):
    db = DB(conn)

    db.fetchMany("SELECT * from items")
    return {"message": "the backend is up and running", "status":True}


@router.get("/item/{item_id}")
async def get_item(item_id: int):
    if item_id <= 0:
        return StandardResponse("invalid item id").error(ErrorCodes.NotFound)

    return StandardResponse("ok", data={"item" : item_id}).success()


@router.post("/item")
async def get_create_item(item: Item):
    return StandardResponse("ok", data={"item_id" : item_id}).success()