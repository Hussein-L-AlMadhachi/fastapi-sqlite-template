from db import DB
from model.items.dto import Item
import aiosqlite


class ItemsService:
    db: DB

    def __init__(self, conn: aiosqlite.Connection):
        self.db = DB(conn)

    async def create(self, item: Item) -> int:
        return await self.db.insert("items", item.model_dump())


    async def listAll(self) -> list[dict]:
        return await self.db.fetchAll("SELECT * FROM items")

    async def fetch(self, item_id: int) -> dict | None:
        return await self.db.fetchOne("SELECT * FROM items WHERE id = ?", item_id)
