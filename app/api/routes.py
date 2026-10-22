import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi import APIRouter, Response, UploadFile, File, HTTPException, Header
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.rag.workflow import ask
from app.rag.vectorstore import add_documents
from app.services.ingestion import load_file, chunk_documents, SUPPORTED
from app.services.audit import write_audit

router = APIRouter(prefix="/api")
settings = get_settings()

# Chunk counts are remembered in a small sidecar file inside the upload folder.
CHUNK_INDEX = Path(settings.upload_dir) / ".chunks.json"


def _chunk_counts() -> dict:
    try:
        return json.loads(CHUNK_INDEX.read_text())
    except (FileNotFoundError, ValueError):
        return {}


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)


@router.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name}


@router.post("/chat")
def chat(payload: ChatRequest):
    try:
        result = ask(payload.question)
        write_audit(payload.question, result["source_used"], result.get("trace", []))

        return {
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/documents")
def list_documents(response: Response):
    response.headers["Cache-Control"] = "no-store"
    upload_dir = Path(settings.upload_dir)
    if not upload_dir.exists():
        return {"documents": []}
    counts = _chunk_counts()
    docs = []
    for p in upload_dir.iterdir():
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            docs.append({
                "name": p.name,
                "chunks": counts.get(p.name),
                "size": p.stat().st_size,
                "uploaded_at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(),
            })
    docs.sort(key=lambda d: d["uploaded_at"], reverse=True)
    return {"documents": docs}


def _doc_path(name: str) -> Path:
    p = Path(settings.upload_dir) / Path(name).name   # .name blocks path traversal
    if not p.is_file() or p.suffix.lower() not in SUPPORTED:
        raise HTTPException(status_code=404, detail="Document not found")
    return p


@router.get("/documents/{name}")
def open_document(name: str):
    p = _doc_path(name)
    return FileResponse(p, filename=p.name, content_disposition_type="inline")


@router.delete("/documents/{name}")
def delete_document_route(name: str, x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    p = _doc_path(name)
    try:
        from app.rag.vectorstore import delete_document   # you add this function
    except ImportError:
        raise HTTPException(status_code=501, detail="Vector deletion is not set up yet, so nothing was deleted")
    try:
        removed = delete_document(p)      # remove its vectors from Pinecone first
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not remove indexed content: {exc}") from exc
    try:
        p.unlink()
    except OSError as exc:   # e.g. Windows keeps the file locked while it is open elsewhere
        raise HTTPException(status_code=500, detail=f"Could not delete the file: {exc}") from exc
    counts = _chunk_counts()
    counts.pop(p.name, None)
    CHUNK_INDEX.write_text(json.dumps(counts))
    return {"message": "Document deleted", "file": p.name, "vectors_deleted": removed}


@router.post("/ingest")
async def ingest(file: UploadFile = File(...), x_admin_key: str = Header(default="")):
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400, detail=f"Supported: {', '.join(sorted(SUPPORTED))}")
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename).name
    dest.write_bytes(await file.read())
    docs = load_file(dest)
    chunks = chunk_documents(docs)
    ids = add_documents(chunks)

    counts = _chunk_counts()          # remember chunk count for the library panel
    counts[dest.name] = len(chunks)
    CHUNK_INDEX.write_text(json.dumps(counts))

    return {"message": "Document indexed", "file": dest.name, "chunks": len(chunks), "ids_created": len(ids)}