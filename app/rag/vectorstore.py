
import time

from pinecone import Pinecone, ServerlessSpec
from langchain_huggingface import HuggingFaceEmbeddings as LocalHuggingFaceEmbeddings
from langchain_pinecone import PineconeVectorStore

from app.core.config import get_settings


settings = get_settings()

_vectorstore = None
_embeddings = None


EMBEDDING_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "all-minilm-l6-v2": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
}


def get_embedding_dimension(model_name: str | None = None) -> int:
    name = (model_name or settings.embedding_model or "").strip()

    if not name:
        raise RuntimeError("Embedding model is not configured")

    normalized = name.lower()

    if normalized in EMBEDDING_DIMENSIONS:
        return EMBEDDING_DIMENSIONS[normalized]

    if "all-minilm" in normalized:
        return 384

    if "text-embedding-3-small" in normalized:
        return 1536

    if "text-embedding-3-large" in normalized:
        return 3072

    if "text-embedding-ada-002" in normalized:
        return 1536

    raise ValueError(f"Unsupported embedding model: {name}")


def get_embeddings():
    global _embeddings

    if _embeddings is None:
        model_name = settings.embedding_model

        if "all-minilm" not in model_name.lower():
            raise ValueError(
                "For local Hugging Face embeddings, set "
                "EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2"
            )

        _embeddings = LocalHuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    return _embeddings


def ensure_index():
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing")

    desired_dimension = get_embedding_dimension()
    pc = Pinecone(api_key=settings.pinecone_api_key)

    names = [item["name"] for item in pc.list_indexes()]

    if settings.pinecone_index_name in names:
        index_info = pc.describe_index(settings.pinecone_index_name)
        current_dimension = getattr(index_info, "dimension", None)

        if current_dimension is None and isinstance(index_info, dict):
            current_dimension = index_info.get("dimension")

        if (
            current_dimension is not None
            and current_dimension != desired_dimension
        ):
            raise RuntimeError(
                f"Pinecone index '{settings.pinecone_index_name}' "
                f"has dimension {current_dimension}, but the configured "
                f"embedding model requires {desired_dimension}. "
                "Create a separate index with the correct dimension "
                "and re-index your documents. Existing index was not deleted."
            )

    else:
        pc.create_index(
            name=settings.pinecone_index_name,
            dimension=desired_dimension,
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1",
            ),
        )

        while not pc.describe_index(
            settings.pinecone_index_name
        ).status["ready"]:
            time.sleep(1)

    return pc.Index(settings.pinecone_index_name)


def get_vectorstore():
    global _vectorstore

    if _vectorstore is None:
        index = ensure_index()

        _vectorstore = PineconeVectorStore(
            index=index,
            embedding=get_embeddings(),
            namespace=settings.pinecone_namespace,
        )

    return _vectorstore


def get_retriever():
    return get_vectorstore().as_retriever(
        search_kwargs={"k": settings.top_k}
    )


def add_documents(chunks):
    store = get_vectorstore()
    return store.add_documents(chunks)



def delete_document(path) -> int:
    """Delete every chunk of one uploaded file from Pinecone. Returns how many were removed."""
    from pathlib import Path
 
    p = Path(path)
    # Loaders may record the source as a relative path, absolute path or bare name.
    candidates = list({str(p), str(p.resolve()), p.as_posix(), p.resolve().as_posix(), p.name})
    flt = {
        "$or": [
            {key: {"$in": candidates}}
            for key in ("source", "file", "file_name", "filename")
        ]
    }
 
    store = get_vectorstore()
    index = store._index
    namespace = settings.pinecone_namespace or ""
    probe = get_embeddings().embed_query(p.name)  # any valid vector works; the filter does the matching
 
    deleted = 0
    for _ in range(20):  # serverless can't delete by filter, so find the IDs first
        res = index.query(
            vector=probe, top_k=1000, filter=flt,
            namespace=namespace, include_metadata=False,
        )
        ids = [m["id"] for m in res["matches"]]
        if not ids:
            break
        for i in range(0, len(ids), 200):
            index.delete(ids=ids[i:i + 200], namespace=namespace)
        deleted += len(ids)
    return deleted