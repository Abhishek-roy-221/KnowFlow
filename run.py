from app.services.ingestion import load_file,chunk_documents
from pathlib import Path
from app.rag.vectorstore import add_documents


docs = load_file(Path("D:\CODING\Agentic Ai\Project\KnowFlow\data\sample_kb\company_it_handbook.md"))

chunks = chunk_documents(docs)

print("Chunks before insertion:", len(chunks))

add_documents(chunks)