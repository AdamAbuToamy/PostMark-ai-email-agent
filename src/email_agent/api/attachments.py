from urllib.parse import quote
import re
from fastapi import HTTPException, Query,Request
from pydantic import BaseModel,ConfigDict,StrictBool
from fastapi.responses import Response
from googleapiclient.errors import HttpError
from email_agent.infrastructure.gmail.content_reader import GmailContentReader


class TextConsent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approved:StrictBool
    sensitive_acknowledged:StrictBool


def register_attachment_routes(app, messages, build_client):
    @app.post('/api/messages/{account_id}/{message_id}/attachments/{part_id}/text')
    def attachment_text(account_id:str,message_id:str,part_id:str,payload:TextConsent,request:Request):
        from email_agent.api.message_management import authorize_local_change
        from email_agent.infrastructure.attachment_text import extract_local
        authorize_local_change(request)
        if account_id not in ('personal','university') or not messages.get(account_id,message_id):raise HTTPException(404,'Message unavailable in this account')
        if not re.fullmatch(r'0(?:\.\d{1,3}){0,20}',part_id):raise HTTPException(400,'Invalid MIME part')
        if not payload.approved or not payload.sensitive_acknowledged:raise HTTPException(400,'Confirm sensitive attachment handling; local extraction only, never external upload')
        try:
            data,mime,_=GmailContentReader(build_client(account_id)).attachment(message_id,part_id,False)
            text=extract_local(data,mime)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        except (LookupError,HttpError) as exc:raise HTTPException(502,'Attachment unavailable') from exc
        from fastapi.responses import JSONResponse
        return JSONResponse(dict(text=text,source='local_attachment',body_complete=False,external_upload=False),headers={'Cache-Control':'no-store'})

    @app.get("/api/messages/{account_id}/{message_id}/attachments/{part_id}")
    def attachment(account_id: str, message_id: str, part_id: str, inline: bool = Query(False)):
        if account_id not in ("personal", "university"):
            raise HTTPException(400, "Unknown account")
        if messages.get(account_id, message_id) is None:
            raise HTTPException(404, "Message not found in this account")
        if not re.fullmatch(r"0(?:\.\d{1,3}){0,20}", part_id):
            raise HTTPException(400, "Invalid MIME part ID")
        try:
            data, mime, filename = GmailContentReader(build_client(account_id)).attachment(message_id, part_id, inline)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except HttpError as exc:
            raise HTTPException(502, "Gmail attachment unavailable") from exc
        return Response(data, media_type=mime, headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
            "Cross-Origin-Resource-Policy": "same-origin",
        })
