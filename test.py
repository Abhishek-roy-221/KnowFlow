from app.rag.vectorstore import get_retriever

retriever = get_retriever()

docs = retriever.invoke(
    "How do I connect to the company VPN?"
)

print(f"Retrieved {len(docs)} documents\n")

for i, doc in enumerate(docs, 1):
    print(f"--- Document {i} ---")
    print(doc.page_content[:500])
    print("Metadata:", doc.metadata)
    print()