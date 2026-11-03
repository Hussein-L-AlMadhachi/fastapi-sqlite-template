import handlers.index


def attach(app):
    app.include_router(handlers.index.router)
