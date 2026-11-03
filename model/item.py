from pydantic import BaseModel



class Item(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None



class ItemService:
    def create():
        pass

    def listAll():
        pass
