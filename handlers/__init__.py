import handlers.items
import handlers.index

def attach(app):
    app.include_router(handlers.items.router,prefix="/api/items")
    app.include_router(handlers.index.router)
