
"""Indexe le catalogue CSV et les documents métier dans ChromaDB."""

import re
from pathlib import Path

from psycopg2.extras import RealDictCursor

import config
import rag
from database import get_connection

BASE_DIR = Path(__file__).parent
KNOWLEDGE_DIR = BASE_DIR / "data" / "knowledge"


def display_value(value: object) -> str:
    if value is None or value == []:
        return "non renseigné"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def build_aroma_document(row: dict) -> str:
    """Construit une description textuelle de chaque arôme."""
    return (
        f"Arôme : {row['nom']}. "
        f"Famille : {row['famille']}. "
        f"Notes : {display_value(row['notes'])}. "
        f"Applications : {display_value(row['applications'])}. "
        f"Sucrosité : {display_value(row['sucrosite'])}. "
        f"Tenue à la chaleur : {display_value(row['tenue_chaleur'])}. "
        f"Certifications : {display_value(row['certifications'])}. "
        f"Prix indicatif : {display_value(row['prix_indicatif_eur_kg'])} "
        "euros/kg. "
        f"Délai : {display_value(row['delai_jours'])} jours. "
        f"Volume minimum : {display_value(row['volume_minimum_kg'])} kg. "
        f"Source : {display_value(row['source_information'])}."
    )


def split_text(text: str, max_chars: int = 800) -> list[str]:
    """Découpe un document en morceaux."""
    paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in text.splitlines()
        if paragraph.strip()
    ]

    chunks = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""

            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start:start + max_chars])
            continue

        candidate = f"{current}\n{paragraph}" if current else paragraph

        if len(candidate) > max_chars:
            if current:
                chunks.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def load_aromas():
    """Lit les arômes depuis PostgreSQL et prépare leurs documents."""
    connection = get_connection()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT * FROM public.food_aromas ORDER BY aroma_id"
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    documents = []
    ids = []
    metadatas = []

    for row in rows:
        source_id = str(row["aroma_id"]).strip()

        if not source_id:
            continue

        documents.append(build_aroma_document(row))
        ids.append(source_id)
        metadatas.append({
            "source_type": "arome",
            "source_id": source_id,
            "is_synthetic": bool(row["is_synthetic"]),
        })

    if not documents:
        raise ValueError("La table public.food_aromas ne contient aucun arôme.")

    return ids, documents, metadatas


def load_knowledge_documents():
    """Prépare tous les fichiers texte du dossier knowledge."""
    if not KNOWLEDGE_DIR.exists():
        raise FileNotFoundError(
            f"Dossier de connaissances introuvable : {KNOWLEDGE_DIR}"
        )

    ids = []
    documents = []
    metadatas = []

    for file_path in sorted(KNOWLEDGE_DIR.glob("*.txt")):
        content = file_path.read_text(encoding="utf-8-sig").strip()

        if not content:
            print(f"Document vide ignoré : {file_path.name}")
            continue

        chunks = split_text(content)

        for index, chunk in enumerate(chunks):
            source_id = f"knowledge_{file_path.stem}_{index}"

            ids.append(source_id)
            documents.append(chunk)

            metadatas.append({
                "source_type": "document",
                "source_id": source_id,
                "is_synthetic": True,
            })

        print(
            f"Document chargé : {file_path.name} "
            f"({len(chunks)} morceaux)"
        )

    return ids, documents, metadatas


def main():
    aroma_ids, aroma_docs, aroma_meta = load_aromas()
    knowledge_ids, knowledge_docs, knowledge_meta = (
        load_knowledge_documents()
    )

    ids = aroma_ids + knowledge_ids
    documents = aroma_docs + knowledge_docs
    metadatas = aroma_meta + knowledge_meta

    if not documents:
        raise ValueError("Aucun document à indexer.")

    print(f"\nArômes : {len(aroma_docs)}")
    print(f"Documents métier : {len(knowledge_docs)}")
    print(f"Total à indexer : {len(documents)}")
    print(f"Calcul des embeddings avec {config.EMBED_MODEL}...")

    embeddings = rag.embed_documents(documents)
    collection = rag.get_collection()

    existing_aromas = collection.get(
        where={"source_type": "arome"},
        include=["metadatas"],
    )
    current_ids = set(ids)
    stale_ids = [
        source_id
        for source_id in existing_aromas["ids"]
        if source_id not in current_ids
    ]

    if stale_ids:
        collection.delete(ids=stale_ids)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    print("\nIndexation terminée !")
    print(f"Collection : {config.COLLECTION_NAME}")
    print(f"Nombre total de documents : {collection.count()}")
    print(f"Emplacement : {config.CHROMA_DIR}")


if __name__ == "__main__":
    main()
