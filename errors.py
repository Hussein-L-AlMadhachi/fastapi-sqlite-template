from lib.http_errors import HttpError

class ErrorCodes:
    NotFound = HttpError("404 not found", 404)
    Forbidden = HttpError("Access is forbidden. contact administrator", 403)
    BadRequest = HttpError("bad request", 400)
    UserLoggedOut = HttpError("you have to login first", 403)
