"""Save conversations about a lesson that were held in an outside LLM.

``POST /api/conversations/import`` takes the JSON form a lesson's context export
asks the outside LLM to produce, or a plain-text copy of the chat. It is
authorized by the import token the export carries (in the body, anywhere in a
pasted chat, or as ``Authorization: Bearer``), so it works from curl or a script
without a browser session. The login gate lets this one path through; this
route enforces the token instead.

Without a token, a signed-in visitor (or any visitor on a trusted local deployment) may import by
naming ``document_id`` in the JSON body, subject to the lesson page's own access
check.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import conversation_import as ci
import web as _web

router = APIRouter()


@router.post(ci.IMPORT_PATH)
async def import_conversation(request: Request):
    """Store an outside conversation's exchanges in the lesson's Q&A log."""
    if not _web._db:
        return JSONResponse({"error": "Database not configured"}, status_code=503)
    raw = await request.body()
    try:
        submission = ci.parse_submission(raw)
        raw_text = raw.decode("utf-8")
        header = request.headers.get("authorization", "")
        bearer = header[7:].strip() if header.lower().startswith("bearer ") else None
        if bearer or submission.token:
            result = ci.store_with_token(_web._db, _web._auth_mod.COOKIE_SECRET, token=bearer,
                                         submission=submission, raw=raw_text, channel="rest")
        else:
            user = _web._set_source_access(request)
            if not user and not _web._is_trusted_local(request):
                return JSONResponse({"error": "Send the import token from the lesson export, or sign in"},
                                    status_code=401)
            document_id = submission.document_id
            if not document_id or not _web._db.is_document_accessible(document_id):
                return JSONResponse({"error": "Name an accessible lesson in document_id, or send the "
                                              "import token from its export"}, status_code=400)
            result = ci.store(_web._db, document_id=document_id,
                              user_id=user.get("user_id") if user else None,
                              profile_key=submission.profile_key, export_id="web",
                              submission=submission, channel="rest", raw=raw_text)
    except ci.TokenError as e:
        return JSONResponse({"error": str(e)}, status_code=401)
    except ci.ConversationError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    result["lesson_url"] = f"{_web._auth_mod._base_url(request)}/lesson/{result['document_id']}"
    return JSONResponse(result)
