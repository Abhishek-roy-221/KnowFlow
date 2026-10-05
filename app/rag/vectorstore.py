import math
import time

from pinecone import Pinecone, ServerlessSpec
from langchain_core.embeddings import Embeddings
from langchain_pinecone import PineconeVectorStore
from huggingface_hub import InferenceClient

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

    if "text-embedding-3-small" in normalized:
        return 1536

    if "text-embedding-3-large" in normalized:
        return 3072

    if "text-embedding-ada-002" in normalized:
        return 1536

    if "all-minilm" in normalized:
        return 384

    raise ValueError(
        f"Unsupported embedding model '{model_name or settings.embedding_model}'."
    )


class HuggingFaceEmbeddings(Embeddings):

    def __init__(self):
        if not settings.hf_token:
            raise RuntimeError("HF_TOKEN is missing")

        self.client = InferenceClient(
            provider="hf-inference",
            api_key=settings.hf_token,
        )

        self.model = settings.embedding_model

    def _normalize(self, vector):
        magnitude = math.sqrt(sum(value * value for value in vector))

        if magnitude == 0:
            return vector

        return [value / magnitude for value in vector]

    def _embed(self, texts):
        if not texts:
            return []

        result = self.client.feature_extraction(
            texts,
            model=self.model,
        )

        vectors = result.tolist() if hasattr(result, "tolist") else result

        embeddings = []

        for vector in vectors:
            if vector and isinstance(vector[0], list):
                pooled = [
                    sum(token[i] for token in vector) / len(vector)
                    for i in range(len(vector[0]))
                ]
                vector = pooled

            vector = self._normalize(vector)

            expected_dimension = get_embedding_dimension(self.model)

            if len(vector) != expected_dimension:
                raise RuntimeError(
                    f"Hugging Face returned {len(vector)} dimensions, "
                    f"expected {expected_dimension}."
                )

            embeddings.append(vector)

        return embeddings

    def embed_documents(self, texts):
        return self._embed(texts)

    def embed_query(self, text):
        return self._embed([text])[0]


def get_embeddings():
    global _embeddings

    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings()

    return _embeddings


def ensure_index():
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing")

    desired_dimension = get_embedding_dimension()

    pc = Pinecone(api_key=settings.pinecone_api_key)

    names = [x["name"] for x in pc.list_indexes()]

    if settings.pinecone_index_name in names:
        index_info = pc.describe_index(settings.pinecone_index_name)

        current_dimension = getattr(index_info, "dimension", None)

        if current_dimension is None and isinstance(index_info, dict):
            current_dimension = index_info.get("dimension")

        if (
            current_dimension is not None
            and current_dimension != desired_dimension
        ):
            pc.delete_index(name=settings.pinecone_index_name)

            while settings.pinecone_index_name in [
                x["name"] for x in pc.list_indexes()
            ]:
                time.sleep(1)

    if settings.pinecone_index_name not in [
        x["name"] for x in pc.list_indexes()
    ]:
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