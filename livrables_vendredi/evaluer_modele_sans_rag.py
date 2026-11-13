"""Evaluate Qwen without catalogue retrieval on the same reference questions."""

import json
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent
CASES_PATH = PROJECT_ROOT / "livrables_jeudi" / "cas_reference_30.jsonl"
CATALOGUE_PATH = OUTPUT_DIR / "catalogue_api.json"
sys.path.insert(0, str(PROJECT_ROOT))

import api
import config
import rag


def load_json_lines(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn"
    )


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * value
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def no_requirement_history() -> list[dict[str, str]]:
    return [
        {
            "role": "assistant",
            "content": api.CLARIFICATION_QUESTIONS["heat"],
        },
        {"role": "user", "content": "Je ne sais pas"},
        {
            "role": "assistant",
            "content": api.CLARIFICATION_QUESTIONS["certifications"],
        },
        {"role": "user", "content": "Aucune exigence de certification"},
    ]


def main() -> None:
    cases = load_json_lines(CASES_PATH)
    if len(cases) != 30:
        raise ValueError(f"30 cas attendus, {len(cases)} trouvés.")

    catalogue = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    names_by_id = {
        str(row["aroma_id"]): str(row["nom"])
        for row in catalogue
        if row.get("aroma_id") is not None and row.get("nom") is not None
    }

    results = []
    elapsed_values = []
    target_name_mentions = 0
    for case in cases:
        target_name = names_by_id.get(case["expected_target_id"])
        if target_name is None:
            raise ValueError(
                f"Cible absente de la réponse archivée de /aromes : "
                f"{case['expected_target_id']}"
            )

        messages = [{"role": "system", "content": rag.SYSTEM_PROMPT}]
        messages.extend(no_requirement_history())
        messages.append({"role": "user", "content": case["question"]})

        started = time.perf_counter()
        answer = rag.chat(messages)
        elapsed_ms = (time.perf_counter() - started) * 1000
        target_mentioned = normalize(target_name) in normalize(answer)
        target_name_mentions += target_mentioned
        elapsed_values.append(elapsed_ms)
        results.append({
            "case_id": case["id"],
            "question": case["question"],
            "expected_target_id": case["expected_target_id"],
            "expected_target_name_from_aromes_api": target_name,
            "target_name_mentioned": target_mentioned,
            "answer": answer,
            "retrieved_context_provided": False,
            "latency_ms": round(elapsed_ms, 2),
        })
        print(
            f"{case['id']}: target_name_mentioned={target_mentioned}, "
            f"{elapsed_ms:.0f} ms"
        )

    metrics = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "condition": "Qwen sans RAG ni catalogue fourni au modèle",
        "llm_model": config.LLM_MODEL,
        "dataset": CASES_PATH.name,
        "case_count": len(cases),
        "retrieved_context_provided": False,
        "target_name_mentioned_count": target_name_mentions,
        "target_name_mentioned_rate": target_name_mentions / len(cases),
        "mean_latency_ms": round(statistics.mean(elapsed_values), 2),
        "median_latency_ms": round(statistics.median(elapsed_values), 2),
        "p95_latency_ms": round(percentile(elapsed_values, 0.95), 2),
        "scope_note": (
            "La cible est reconnue uniquement si son libellé exact, obtenu "
            "depuis l'archive de GET /aromes, apparaît dans la réponse du "
            "modèle sans RAG. Cette mesure lexicale est conservative et ne "
            "mesure pas une équivalence sémantique."
        ),
    }

    (OUTPUT_DIR / "resultats_sans_rag.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT_DIR / "metriques_sans_rag.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("\nMETRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
