from logs import logger


from fastapi import HTTPException
from errors import ErrorCodes
from lib.http_errors import GenerateErrorResponses



errors_map = GenerateErrorResponses(ErrorCodes)


class StandardResponse:
    msg : str
    data : dict|list|None
    errcode: str
    errmsg: str

    def __init__(self, msg: str, data: dict|list|None = None):
        self.msg = msg
        self.data = data

    def success(self):
        return { 
            "detail": {
                "message": self.msg,
                "data": self.data
            }
        }

    def logInfo(self, action_name:str , msg: str, info=None):
        logger.Info( action_name, msg = msg, info=info )
        return self

    def error(self, action_name:str, err):
        logger.error( action_name, error = str(err), info=self.data )

        error_obj = errors_map.get(self.msg)
        if error_obj is None:
            error_obj = errors_map.get("default")
            self.msg = "interal_error" # create a new label to avoid exposing data

        raise HTTPException(
            status_code = error_obj.code,
            detail = {
                "errcode": self.msg,
                "message": error_obj.msg
            }
        )

    def nolog_error(self):
        error_obj = errors_map.get(self.msg)
        if error_obj is None:
            error_obj = errors_map.get("default")
            self.msg = "interal_error" # create a new label to avoid exposing data

        raise HTTPException(
            status_code = error_obj.code,
            detail = {
                "errcode": self.msg,
                "message": error_obj.msg
            }
        )

