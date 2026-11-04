from fastapi import APIRouter, Depends
import aiosqlite

from db import get_db
from lib.response import StandardResponse
from model.items.dto import Item
from model.items.service import ItemsService

router = APIRouter()


@router.get("/")
async def root(conn: aiosqlite.Connection = Depends(get_db)):
    items = await ItemsService(conn).listAll()
    return StandardResponse("ok", data=items).success()


@router.get("/item/{item_id}")
async def get_item(item_id: int, conn: aiosqlite.Connection = Depends(get_db)):
    if item_id <= 0:
        StandardResponse("NotFound").nolog_error()

    item = await ItemsService(conn).fetch(item_id)
    if item is None:
        StandardResponse("NotFound").nolog_error()

    return StandardResponse("ok", data={"item": item}).success()


@router.post("/item")
async def create_item(item: Item, conn: aiosqlite.Connection = Depends(get_db)):
    item_id = await ItemsService(conn).create(item)
    return StandardResponse("ok", data={"item_id": item_id}).success()
