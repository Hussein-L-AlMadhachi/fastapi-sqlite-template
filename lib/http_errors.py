class HttpError:
    code: int
    msg: str

    def __init__(self, msg: str, code: int):
        self.code = code
        self.msg = msg


def GenerateErrorResponses( errors_obj ):
    errors_map = {"default": HttpError("operation failed. try again later.", 500)}


    for name, value in vars(errors_obj).items():
        if not name.startswith("__") and not name.startswith("_"):
            errors_map[name] = value

    return errors_map
