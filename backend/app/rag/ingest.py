import re
from dataclasses import dataclass
from pathlib import Path

import chromadb

POLICIES_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"
CHROMA_DIR = Path(__file__).resolve().parents[2] / "chroma_db"
COLLECTION_NAME = "policies"


@dataclass(frozen=True)
class PolicyChunk:
    source: str
    heading: str
    text: str

    @property
    def id(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.heading.lower()).strip("-")
        return f"{self.source}::{slug}"


def load_policy_chunks() -> list[PolicyChunk]:
    chunks = []
    for path in sorted(POLICIES_DIR.glob("*.md")):
        sections = re.split(r"(?m)^## ", path.read_text())
        for section in sections[1:]:  # sections[0] is the doc title before the first "##"
            heading, _, body = section.partition("\n")
            chunks.append(PolicyChunk(source=path.stem, heading=heading.strip(), text=body.strip()))
    return chunks


def ingest() -> int:
    """Chunks the policy markdown docs by heading and upserts them into the Chroma `policies` collection."""
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(COLLECTION_NAME)
    chunks = load_policy_chunks()
    collection.upsert(
        ids=[chunk.id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        metadatas=[{"source": chunk.source, "heading": chunk.heading} for chunk in chunks],
    )
    return len(chunks)


if __name__ == "__main__":
    ingested = ingest()
    print(f"Ingested {ingested} policy chunks into {CHROMA_DIR}")
