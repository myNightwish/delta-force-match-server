import re
MESSAGE_KEY = re.compile(r"^[a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
def error_code(status, detail):
    message=detail if isinstance(detail,str) else detail.get('message','') if isinstance(detail,dict) else ''
    if MESSAGE_KEY.fullmatch(message):return message
    return (detail.get('code') if isinstance(detail,dict) else None) or {400:'error.invalidRequest',401:'error.authentication',403:'error.forbidden',404:'error.notFound',409:'error.stateConflict',422:'message.validation_failed',429:'error.rateLimited'}.get(status,'error.unavailable')
