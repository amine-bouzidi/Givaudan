"""Evaluate the real HTTP /ask responses for the 30 reference cases."""

import argparse
import csv
import json
import os
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(__file__).resolve().parent
CASES_PATH = PROJECT_ROOT / "livrables_jeudi" / "cas_reference_30.jsonl"
API_URL = os.getenv("UMI_API_URL", "http://127.0.0.1:8001").rstrip("/")
TIMEOUT_SECONDS = 180
sys.path.insert(0, str(PROJECT_ROOT))

import api


def load_cases() -> list[dict]:
    with CASES_PATH.open(encoding="utf-8") as file:
        cases = [json.loads(line) for line in file if line.strip()]
    if len(cases) != 30:
        raise ValueError(f"30 cas attendus, {len(cases)} trouvés.")
    return cases


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


def get_api_version() -> str:
    request = Request(f"{API_URL}/openapi.json")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            specification = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Impossible de lire le schéma OpenAPI sur {API_URL}: {exc}"
        ) from exc
    return str(specification.get("info", {}).get("version", "inconnue"))


def get_catalogue() -> list[dict]:
    request = Request(f"{API_URL}/aromes")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            catalogue = json.load(response)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Impossible de lire le catalogue via {API_URL}/aromes: {exc}"
        ) from exc
    if not isinstance(catalogue, list):
        raise ValueError("La réponse de /aromes n'est pas une liste.")
    return catalogue


def call_ask(case: dict) -> tuple[dict, int, float]:
    payload = {
        "question": case["question"],
        "top_k": 4,
        "history": no_requirement_history(),
    }
    request = Request(
        f"{API_URL}/ask",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            http_status = response.status
            result = json.load(response)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Erreur HTTP {exc.code} pour {case['id']}: {body}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Échec de l'appel /ask pour {case['id']}: {exc}"
        ) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    return result, http_status, elapsed_ms


def source_is_compliant(source: dict, case: dict) -> bool:
    return (
        source.get("famille") == case["expected_family"]
        and case["expected_application"] in source.get("applications", [])
        and source.get("sucrosite") == case["expected_sucrosite"]
        and (
            case["expected_heat"] is None
            or source.get("tenue_chaleur") == case["expected_heat"]
        )
    )


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Évalue les réponses HTTP de l'API Umi."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Dossier de sortie pour isoler les résultats d'une version API.",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases()
    api_version = get_api_version()
    catalogue = get_catalogue()
    catalogue_metrics = {
        "row_count": len(catalogue),
        "synthetic_row_count": sum(
            row.get("is_synthetic") is True for row in catalogue
        ),
    }
    catalogue_metrics["synthetic_row_rate"] = (
        catalogue_metrics["synthetic_row_count"] / len(catalogue)
        if catalogue
        else None
    )
    names_by_id = {
        str(row["aroma_id"]): str(row["nom"])
        for row in catalogue
        if row.get("aroma_id") is not None and row.get("nom") is not None
    }
    results = []
    elapsed_values = []
    status_counts: dict[str, int] = {}
    target_hits = 0
    target_name_mentions = 0
    recommendation_count = 0
    recommendation_target_hits = 0
    compliant_sources = 0
    total_sources = 0
    synthetic_sources = 0

    for case in cases:
        response, http_status, elapsed_ms = call_ask(case)
        sources = response.get("sources", [])
        source_ids = [str(source.get("source_id", "")) for source in sources]
        target_hit = case["expected_target_id"] in source_ids
        target_name = names_by_id.get(case["expected_target_id"])
        if target_name is None:
            raise ValueError(
                f"Cible absente de la réponse de /aromes : "
                f"{case['expected_target_id']}"
            )
        target_name_mentioned = (
            normalize(target_name)
            in normalize(str(response.get("answer", "")))
        )
        is_recommendation = response.get("status") == "recommendations_ready"
        compliant_count = sum(
            source_is_compliant(source, case) for source in sources
        )

        target_hits += target_hit
        target_name_mentions += target_name_mentioned
        recommendation_count += is_recommendation
        recommendation_target_hits += is_recommendation and target_hit
        compliant_sources += compliant_count
        total_sources += len(sources)
        synthetic_sources += sum(
            source.get("is_synthetic") is True for source in sources
        )
        elapsed_values.append(elapsed_ms)
        response_status = str(response.get("status", "missing"))
        status_counts[response_status] = status_counts.get(response_status, 0) + 1

        results.append({
            "case_id": case["id"],
            "question": case["question"],
            "expected_target_id": case["expected_target_id"],
            "http_status": http_status,
            "status": response_status,
            "model": response.get("model"),
            "answer": response.get("answer", ""),
            "suggestions": response.get("suggestions", []),
            "source_ids": source_ids,
            "target_in_sources": target_hit,
            "expected_target_name_from_aromes_api": target_name,
            "target_name_mentioned_in_answer": target_name_mentioned,
            "source_count": len(sources),
            "compliant_source_count": compliant_count,
            "sources": sources,
            "latency_ms": round(elapsed_ms, 2),
        })
        print(
            f"{case['id']}: status={response_status}, "
            f"target_in_sources={target_hit}, "
            f"sources={len(sources)}, {elapsed_ms:.0f} ms"
        )

    metrics = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": f"{API_URL}/ask",
        "api_version": api_version,
        "dataset": CASES_PATH.name,
        "case_count": len(cases),
        "catalogue_from_aromes_endpoint": catalogue_metrics,
        "status_counts": status_counts,
        "recommendations_ready_count": recommendation_count,
        "target_in_api_sources_count": target_hits,
        "target_in_api_sources_rate": target_hits / len(cases),
        "target_name_mentioned_in_answer_count": target_name_mentions,
        "target_name_mentioned_in_answer_rate": (
            target_name_mentions / len(cases)
        ),
        "recommendation_target_hit_rate": (
            recommendation_target_hits / recommendation_count
            if recommendation_count
            else None
        ),
        "api_source_constraint_compliance": (
            compliant_sources / total_sources if total_sources else None
        ),
        "api_source_count": total_sources,
        "synthetic_source_rate": (
            synthetic_sources / total_sources if total_sources else None
        ),
        "latency_ms_mean": round(statistics.mean(elapsed_values), 2),
        "latency_ms_median": round(statistics.median(elapsed_values), 2),
        "latency_ms_p95": round(percentile(elapsed_values, 0.95), 2),
        "scope_note": (
            "Mesure des réponses HTTP /ask sur le jeu synthétique de 30 cas. "
            "La latence inclut l'API, l'embedding, le retrieval, le SQL et "
            "la génération Qwen. Ce n'est pas une validation indépendante "
            "ou de production."
        ),
    }

    (output_dir / "metriques_api_ask.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "catalogue_api.json").write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "resultats_api_ask.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "resultats_api_ask.csv").open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows({
            **item,
            "suggestions": json.dumps(item["suggestions"], ensure_ascii=False),
            "source_ids": json.dumps(item["source_ids"], ensure_ascii=False),
            "sources": json.dumps(item["sources"], ensure_ascii=False),
        } for item in results)

    print("\nMETRICS")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
