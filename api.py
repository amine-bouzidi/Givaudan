
"""API Umi : PostgreSQL + ChromaDB + Ollama."""

import json
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from psycopg2.extras import RealDictCursor

import config
import rag
from database import get_connection


# --------------------------------------------------
# JOURNALISATION
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

logger = logging.getLogger("umi")
HISTORY_LIMIT = 24

# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

app = FastAPI(
    title="Umi API",
    description="Assistant aromatique conversationnel avec RAG.",
    version="6.3.0",
)


# --------------------------------------------------
# MODÈLES
# --------------------------------------------------

class ConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class SearchRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: int = Field(config.TOP_K, ge=1, le=20)
    history: list[ConversationMessage] = Field(
        default_factory=list,
        max_length=HISTORY_LIMIT,
    )


class AromaSource(BaseModel):
    source_type: str
    source_id: str
    is_synthetic: bool
    text: str
    score: float = Field(
        ...,
        description="Score associé à la source",
    )
    nom: str | None = None
    famille: str | None = None
    notes: str | None = None
    applications: list[str] = Field(default_factory=list)
    sucrosite: int | None = None
    tenue_chaleur: str | None = None
    prix: float | None = None
    delai: int | None = None


class AskResponse(BaseModel):
    question: str
    status: Literal["needs_clarification", "recommendations_ready"]
    answer: str
    model: str
    sources: list[AromaSource]
    suggestions: list[str] = Field(default_factory=list)


# --------------------------------------------------
# NORMALISATION
# --------------------------------------------------

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text).lower())

    return "".join(
        char
        for char in text
        if unicodedata.category(char) != "Mn"
    )


def get_conversation_text(
    question: str,
    history: list[dict],
) -> str:
    messages = []
    conversation_messages = [
        item
        for item in history[-HISTORY_LIMIT:]
        if isinstance(item, dict)
    ] + [{"role": "user", "content": question}]

    for item in conversation_messages:
        if (
            item.get("role") == "user"
            and isinstance(item.get("content"), str)
        ):
            messages.append(item["content"])

    for index, item in enumerate(conversation_messages[:-2]):
        if item.get("role") != "assistant":
            continue

        answer = conversation_messages[index + 1]
        if answer.get("role") != "user":
            continue

        slot = get_question_slot(str(item.get("content", "")))
        answer_text = str(answer.get("content", ""))
        normalized_answer = normalize(answer_text).strip()

        if slot == "sweetness" and re.fullmatch(r"[1-5]", normalized_answer):
            messages.append(f"sucrosite = {normalized_answer}")
        elif slot == "heat" and not is_no_requirement(answer_text):
            if normalized_answer in {"oui", "oui cuisson", "oui pasteurisation"}:
                messages.append("pasteurisation")
            else:
                messages.append(answer_text)

    last_assistant = next(
        (
            item
            for item in reversed(history)
            if item.get("role") == "assistant"
        ),
        None,
    )
    if last_assistant:
        slot = get_question_slot(str(last_assistant.get("content", "")))
        normalized_question = normalize(question).strip()
        if slot == "sweetness" and re.fullmatch(r"[1-5]", normalized_question):
            messages.append(f"sucrosite = {normalized_question}")
        elif slot == "heat" and not is_no_requirement(question):
            if normalized_question in {"oui", "oui cuisson", "oui pasteurisation"}:
                messages.append("pasteurisation")

    return " ".join(messages)


def get_question_slot(question: str) -> str | None:
    normalized_question = normalize(question)
    if any(
        keyword in normalized_question
        for keyword in ("chaleur", "pasteurisation", "cuisson")
    ):
        return "heat"
    if "produit alimentaire" in normalized_question:
        return "application"
    if (
        "famille aromatique" in normalized_question
        or "univers aromatique" in normalized_question
    ):
        return "family"
    if "sucrosit" in normalized_question:
        return "sweetness"
    if "certification" in normalized_question:
        return "certifications"
    return None


def is_no_requirement(answer: str) -> bool:
    normalized_answer = re.sub(
        r"[^\w\s]",
        " ",
        normalize(answer),
    )
    normalized_answer = " ".join(normalized_answer.split())
    no_requirement_phrases = (
        "aucune",
        "aucun",
        "pas de preference",
        "peu importe",
        "pas d exigence",
        "sans exigence",
        "je ne sais pas",
        "je sais pas",
        "je ne l ai pas",
        "je l ai pas",
        "pas de contrainte",
        "sans contrainte",
        "pas la contrainte",
        "pas de chaleur",
        "pas de cuisson",
        "sans cuisson",
        "pas de pasteurisation",
        "sans pasteurisation",
    )
    return (
        normalized_answer in {"non", "non merci"}
        or any(
            phrase in normalized_answer
            for phrase in no_requirement_phrases
        )
    )


# --------------------------------------------------
# DÉTECTION DES PRÉFÉRENCES SENSORIELLES
# --------------------------------------------------

# --------------------------------------------------
# DÉTECTION DE LA FAMILLE
# --------------------------------------------------

def detect_family(question: str) -> str | None:
    q = normalize(question)

    families = {
        "fruits rouges": "Fruits rouges",
        "fruit rouge": "Fruits rouges",
        "fraise": "Fruits rouges",
        "framboise": "Fruits rouges",
        "cerise": "Fruits rouges",

        "fruits tropicaux": "Fruits tropicaux",
        "fruit tropical": "Fruits tropicaux",
        "fruit exotique": "Fruits tropicaux",
        "fruits exotiques": "Fruits tropicaux",
        "exotique": "Fruits tropicaux",
        "mangue": "Fruits tropicaux",
        "ananas": "Fruits tropicaux",
        "passion": "Fruits tropicaux",

        "agrum": "Agrumes",
        "citron": "Agrumes",
        "orange": "Agrumes",
        "pamplemousse": "Agrumes",
        "mandarine": "Agrumes",

        "gourmand": "Notes gourmandes",
        "vanille": "Notes gourmandes",
        "caramel": "Notes gourmandes",
        "chocolat": "Notes gourmandes",

        "epice": "Herbes et épices",
        "herbe": "Herbes et épices",
        "gingembre": "Herbes et épices",
        "cardamome": "Herbes et épices",
        "cannelle": "Herbes et épices",
        "poivre": "Herbes et épices",

        "sale": "Notes salées",
        "umami": "Notes salées",
        "bouillon": "Notes salées",
    }

    for keyword in sorted(families, key=len, reverse=True):
        if keyword in q:
            return families[keyword]

    return None


# --------------------------------------------------
# DÉTECTION DE L'APPLICATION
# --------------------------------------------------

def detect_application(question: str) -> str | None:
    q = normalize(question)

    applications = {
        "boisson": "boissons",
        "biscuit": "biscuits",
        "confiserie": "confiserie",
        "dessert": "desserts",
        "yaourt": "produits laitiers",
        "produit laitier": "produits laitiers",
        "glace": "glaces",
    }

    for keyword in sorted(applications, key=len, reverse=True):
        if keyword in q:
            return applications[keyword]

    return None


# --------------------------------------------------
# DÉTECTION DE LA SUCROSITÉ
# --------------------------------------------------

def detect_sweetness_filter(question: str) -> list[dict]:
    q = normalize(question)

    def make_filter(operator: str, value: int) -> list[dict]:
        return [{
            "column": "sucrosite",
            "label": "sucrosité",
            "operator": operator,
            "value": Decimal(str(value)),
        }]

    # Niveau 1 : absence ou très faible sucrosité
    very_low = [
        "sans sucre",
        "non sucre",
        "pas sucre",
        "tres peu sucre",
        "extremement peu sucre",
        "sucrosite minimale",
        "sucrosite de 1",
        "niveau de sucre 1",
    ]

    # Niveaux 1 à 3 : faible sucrosité
    low = [
        "pas trop sucre",
        "peu sucre",
        "faiblement sucre",
        "moins sucre",
        "peu de sucre",
        "faible sucrosite",
        "legerement sucre",
        "sucrosite faible",
    ]

    # Niveau 3 : sucrosité moyenne
    medium = [
        "moyennement sucre",
        "equilibre en sucre",
        "sucrosite moyenne",
        "niveau de sucre moyen",
        "sucrosite de 3",
        "niveau de sucre 3",
    ]

    # Niveaux 4 à 5 : forte sucrosité
    high = [
        "bien sucre",
        "tres sucre",
        "fortement sucre",
        "beaucoup de sucre",
        "forte sucrosite",
        "arome sucre",
        "arome tres sucre",
        "plutot sucre",
        "plus sucre",
        "sucrosite elevee",
        "sucrosite forte",
        "sucrosite de 4",
        "niveau de sucre 4",
    ]

    # Niveau 5 : sucrosité maximale
    very_high = [
        "extremement sucre",
        "tres fortement sucre",
        "tres riche en sucre",
        "sucrosite maximale",
        "sucrosite de 5",
        "niveau de sucre 5",
    ]

    # Priorité aux expressions les plus précises
    if any(expression in q for expression in very_low):
        return make_filter("<=", 1)

    if any(expression in q for expression in very_high):
        return make_filter("=", 5)

    if any(expression in q for expression in medium):
        return make_filter("=", 3)

    if any(expression in q for expression in low):
        return make_filter("<=", 3)

    if any(expression in q for expression in high):
        return make_filter(">=", 4)

    # Détection générique d'une demande sucrée
    if re.search(r"\bsucre\b", q):
        return make_filter(">=", 4)

    return []


# --------------------------------------------------
# DÉTECTION DE LA TENUE À LA CHALEUR
# --------------------------------------------------

def detect_heat_filter(question: str) -> str | None:
    q = normalize(question)

    heat_keywords = [
        "pasteurise",
        "pasteurisation",
        "cuisson",
        "haute temperature",
        "haute chaleur",
        "resistant a la chaleur",
        "resistance a la chaleur",
        "tenue a la chaleur",
    ]

    if any(keyword in q for keyword in heat_keywords):
        return "élevée"

    return None


CERTIFICATION_KEYWORDS = (
    "bio",
    "halal",
    "kasher",
    "kosher",
    "vegan",
    "fssc 22000",
    "iso 22000",
    "haccp",
)


def detect_certifications(question: str) -> list[str]:
    q = normalize(question)
    return [
        certification
        for certification in CERTIFICATION_KEYWORDS
        if re.search(r"(?<!\w)" + re.escape(certification) + r"(?!\w)", q)
    ]


# --------------------------------------------------
# DÉTECTION DES CRITÈRES NUMÉRIQUES
# --------------------------------------------------

NUMERIC_FIELDS = {
    "sucrosite": {
        "column": "sucrosite",
        "label": "sucrosité",
        "aliases": ["sucrosite"],
    },
    "prix_indicatif_eur_kg": {
        "column": "prix_indicatif_eur_kg",
        "label": "prix",
        "aliases": [
            "prix au kilo",
            "prix par kilo",
            "prix au kg",
            "prix par kg",
            "prix",
        ],
    },
    "delai_jours": {
        "column": "delai_jours",
        "label": "délai",
        "aliases": ["delai", "delais"],
    },
    "volume_minimum_kg": {
        "column": "volume_minimum_kg",
        "label": "volume minimum",
        "aliases": [
            "volume minimum",
            "volume minimal",
            "quantite minimale",
            "quantite minimum",
        ],
    },
}


OPERATORS = [
    (r">=", ">="),
    (r"<=", "<="),
    (r">", ">"),
    (r"<", "<"),
    (r"=", "="),
    (
        r"(?:superieur(?:e)?\s+ou\s+egal(?:e)?\s+a"
        r"|au\s+moins|minimum\s+de)",
        ">=",
    ),
    (
        r"(?:inferieur(?:e)?\s+ou\s+egal(?:e)?\s+a"
        r"|au\s+plus|maximum\s+de)",
        "<=",
    ),
    (
        r"(?:strictement\s+)?superieur(?:e)?\s+a"
        r"|plus\s+de|au-dessus\s+de",
        ">",
    ),
    (
        r"(?:strictement\s+)?inferieur(?:e)?\s+a"
        r"|moins\s+de|en-dessous\s+de",
        "<",
    ),
    (
        r"(?:egal(?:e)?\s+a|exactement)",
        "=",
    ),
]


def detect_numeric_filters(question: str) -> list[dict]:
    q = normalize(question)
    filters = []

    for field in NUMERIC_FIELDS.values():
        aliases = sorted(
            field["aliases"],
            key=len,
            reverse=True,
        )

        for alias in aliases:
            alias_pattern = (
                r"(?<!\w)"
                + re.escape(alias)
                + r"(?!\w)"
            )

            for operator_pattern, sql_operator in OPERATORS:
                pattern = (
                    alias_pattern
                    + r"\s*(?:est\s*)?"
                    + r"(?:"
                    + operator_pattern
                    + r")"
                    + r"\s*(-?\d+(?:[.,]\d+)?)"
                )

                match = re.search(pattern, q)

                if not match:
                    continue

                raw_value = match.group(1).replace(",", ".")

                try:
                    value = Decimal(raw_value)
                except InvalidOperation:
                    continue

                filters.append({
                    "column": field["column"],
                    "label": field["label"],
                    "operator": sql_operator,
                    "value": value,
                })

                break

            if any(
                item["column"] == field["column"]
                for item in filters
            ):
                break

    return filters


# --------------------------------------------------
# RECHERCHE POSTGRESQL
# --------------------------------------------------

def search_catalogue(
    question: str,
    semantic_documents: list[dict],
    top_k: int,
    ignored_criteria: set[str] | None = None,
) -> tuple[
    list[dict],
    str | None,
    str | None,
    list[dict],
    str | None,
    list[str],
]:

    ignored_criteria = ignored_criteria or set()
    ignore_all_filters = "all" in ignored_criteria
    family = (
        None
        if ignore_all_filters or "family" in ignored_criteria
        else detect_family(question)
    )
    application = (
        None
        if ignore_all_filters or "application" in ignored_criteria
        else detect_application(question)
    )
    numeric_filters = (
        []
        if ignore_all_filters
        else detect_numeric_filters(question)
    )
    if "sweetness" in ignored_criteria and not ignore_all_filters:
        numeric_filters = [
            item
            for item in numeric_filters
            if item["column"] != "sucrosite"
        ]
    certifications = detect_certifications(question)

    sweetness_filters = detect_sweetness_filter(question)

    # Un filtre numérique explicitement demandé est prioritaire.
    if (
        not ignore_all_filters
        and "sweetness" not in ignored_criteria
        and not any(
        item["column"] == "sucrosite"
        for item in numeric_filters
        )
    ):
        numeric_filters.extend(sweetness_filters)

    heat_filter = (
        None
        if ignore_all_filters or "heat" in ignored_criteria
        else detect_heat_filter(question)
    )
    if ignore_all_filters or "certifications" in ignored_criteria:
        certifications = []

    candidate_ids = list(dict.fromkeys(
        str(document["metadata"]["source_id"])
        for document in semantic_documents
        if document.get("metadata", {}).get("source_id")
    ))
    if not candidate_ids:
        logger.error("La recherche vectorielle n'a retourné aucun arôme.")
        raise HTTPException(
            status_code=503,
            detail="Aucun arôme indexé. Lancez `python ingest.py`.",
        )

    similarity_by_id = {
        str(document["metadata"]["source_id"]): document["score"]
        for document in semantic_documents
        if document.get("metadata", {}).get("source_id")
    }

    conn = None

    try:
        conn = get_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM public.food_aromas WHERE 1=1"
            params = []

            query += " AND aroma_id = ANY(%s)"
            params.append(candidate_ids)

            # Filtre famille
            if family:
                query += " AND famille ILIKE %s"
                params.append(family)

            # Filtre application
            if application:
                query += """
                    AND EXISTS (
                        SELECT 1
                        FROM unnest(applications) AS app
                        WHERE app ILIKE %s
                    )
                """
                params.append(f"%{application}%")

            # Filtres numériques
            allowed_columns = {
                "sucrosite",
                "prix_indicatif_eur_kg",
                "delai_jours",
                "volume_minimum_kg",
            }

            allowed_operators = {
                ">", "<", ">=", "<=", "=",
            }

            for criterion in numeric_filters:
                column = criterion["column"]
                operator = criterion["operator"]

                if (
                    column not in allowed_columns
                    or operator not in allowed_operators
                ):
                    continue

                query += f" AND {column} {operator} %s"
                params.append(criterion["value"])

            # Filtre strict de tenue à la chaleur
            if heat_filter:
                query += " AND tenue_chaleur = %s"
                params.append(heat_filter)

            if certifications:
                query += """
                    AND EXISTS (
                        SELECT 1
                        FROM unnest(certifications) AS certification
                        WHERE lower(certification) LIKE ANY(%s)
                    )
                """
                params.append([
                    f"%{certification}%"
                    for certification in certifications
                ])

            cursor.execute(query, params)
            rows = [dict(row) for row in cursor.fetchall()]

        for row in rows:
            row["_similarity_score"] = similarity_by_id.get(
                str(row["aroma_id"]),
                0.0,
            )

        rows.sort(
            key=lambda row: (
                -row["_similarity_score"],
                str(row["nom"]).casefold(),
            )
        )

        return (
            rows[:top_k],
            family,
            application,
            numeric_filters,
            heat_filter,
            certifications,
        )

    except Exception as exc:
        logger.exception("Erreur pendant la recherche PostgreSQL")

        raise HTTPException(
            status_code=503,
            detail="Erreur lors de la recherche dans PostgreSQL.",
        ) from exc

    finally:
        if conn is not None:
            conn.close()


# --------------------------------------------------
# CONVERSATION AVEC LE LLM
# --------------------------------------------------

def format_recommendation_answer(
    rows: list[dict],
    numeric_filters: list[dict],
    heat_filter: str | None,
    introduction: str | None = None,
) -> str:
    lines = [
        introduction
        or "Voici les arômes du catalogue les plus proches de votre demande :"
    ]

    if numeric_filters:
        lines.extend(["", "Critères numériques appliqués :"])
        lines.extend(
            f"- {item['label']} {item['operator']} {item['value']}"
            for item in numeric_filters
        )

    if heat_filter:
        lines.extend([
            "",
            f"Tenue à la chaleur exigée : {heat_filter}.",
        ])

    for index, row in enumerate(rows, start=1):
        lines.extend([
            "",
            f"{index}. {row['nom']} ({row['famille']})",
            f"- Notes : {display_catalogue_value(row['notes'])}",
            f"- Applications : {display_catalogue_value(row['applications'])}",
            f"- Sucrosité : {display_catalogue_value(row['sucrosite'])}",
            "  Tenue à la chaleur : "
            f"{display_catalogue_value(row['tenue_chaleur'])}",
            "  Certifications : "
            f"{display_catalogue_value(row['certifications'])}",
            "  Prix indicatif : "
            f"{display_catalogue_value(row['prix_indicatif_eur_kg'])} €/kg",
            f"  Délai : {display_catalogue_value(row['delai_jours'])} jours",
            "  Volume minimum : "
            f"{display_catalogue_value(row['volume_minimum_kg'])} kg",
        ])

    return "\n".join(lines)


AROMA_OPTION_PATTERN = re.compile(
    r"^\s*(\d+)\.\s*(.+?)\s+\(([^()\n]+)\)\s*$",
    re.MULTILINE,
)

AROMA_OPTION = dict[str, str | int | Decimal | None]


def parse_aroma_options(message: str) -> list[AROMA_OPTION]:
    matches = list(AROMA_OPTION_PATTERN.finditer(message))
    options = []
    for index, match in enumerate(matches):
        block_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(message)
        )
        block = message[match.end():block_end]
        minimum_match = re.search(
            r"volume minimum\s*:\s*(\d+(?:[.,]\d+)?)\s*kg",
            normalize(block),
        )
        minimum = (
            Decimal(minimum_match.group(1).replace(",", "."))
            if minimum_match
            else None
        )
        options.append({
            "number": int(match.group(1)),
            "name": match.group(2).strip(),
            "family": match.group(3).strip(),
            "minimum_volume_kg": minimum,
        })
    return options


def format_selected_options(options: list[AROMA_OPTION]) -> str:
    lines = []
    for index, option in enumerate(options, start=1):
        lines.append(f"{index}. {option['name']} ({option['family']})")
        minimum = option.get("minimum_volume_kg")
        if minimum is not None:
            lines.append(
                f"   Volume minimum : {format_decimal(minimum)} kg"
            )
    return "\n".join(lines)


def format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def match_selected_options(
    question: str,
    options: list[AROMA_OPTION],
) -> list[AROMA_OPTION]:
    normalized_question = normalize(question)
    numbered_references = re.findall(
        r"\b(?:aromes?|options?|choix|numeros?)\s*"
        r"(?:n\s*)?#?\s*(\d+(?:\s*(?:,|et|&)\s*\d+)*)",
        normalized_question,
    )
    selected_numbers = {
        int(value)
        for reference in numbered_references
        for value in re.findall(r"\d+", reference)
    }
    if not numbered_references and re.fullmatch(
        r"\s*#?\d+(?:\s*(?:,|et|&)\s*#?\d+)*\s*",
        normalized_question,
    ):
        selected_numbers = {
            int(value)
            for value in re.findall(r"\d+", normalized_question)
        }
    selected_by_number = [
        option for option in options
        if option["number"] in selected_numbers
    ]

    selected_by_name = [
        option for option in options
        if normalize(str(option["name"])) in normalized_question
    ]
    if selected_by_name:
        selected = selected_by_number + selected_by_name
    else:
        base_name_matches = []
        for option in options:
            base_name = normalize(str(option["name"])).split(" - ", 1)[0].strip()
            if base_name and re.search(
                r"(?<!\w)" + re.escape(base_name) + r"(?!\w)",
                normalized_question,
            ):
                base_name_matches.append(option)

        selected = selected_by_number + (
            base_name_matches if len(base_name_matches) == 1 else []
        )

    unique_numbers = {int(option["number"]) for option in selected}
    return [
        option for option in options
        if int(option["number"]) in unique_numbers
    ]


def parse_percentages(answer: str, expected_count: int) -> list[Decimal] | None:
    values = re.findall(r"(?<!\w)(\d+(?:[.,]\d+)?)\s*%", answer)
    if len(values) != expected_count:
        return None
    try:
        return [Decimal(value.replace(",", ".")) for value in values]
    except InvalidOperation:
        return None


def format_proportions_prompt(
    options: list[AROMA_OPTION],
    proportions: list[Decimal] | None = None,
) -> str:
    selected_options = format_selected_options(options)
    labels = ", ".join(str(option["name"]) for option in options)
    if proportions is None:
        example = ", ".join(["25 %"] * len(options))
        return (
            f"Excellent choix ! Pour préparer votre mélange de "
            f"{len(options)} arômes, indiquez {len(options)} proportions, "
            "une pour chaque arôme ci-dessous. La somme doit être "
            "exactement égale à 100 %. Répondez dans le même ordre "
            f"({labels}), par exemple : {example}.\n{selected_options}"
        )
    total = sum(proportions)
    return (
        f"Les proportions indiquées totalisent {format_decimal(total)} %, "
        "au lieu de 100 %. "
        "La somme doit être égale à 100 %. "
        "Merci de donner une proportion correcte pour chaque arôme, dans "
        f"le même ordre ({labels}).\n{selected_options}"
    )


def extract_selection_from_history(
    history: list[dict],
) -> list[AROMA_OPTION]:
    for item in reversed(history):
        if item.get("role") != "assistant" or not isinstance(
            item.get("content"), str
        ):
            continue
        options = parse_aroma_options(item["content"])
        if options:
            return options
    return []


def format_volume_prompt(
    options: list[AROMA_OPTION],
    proportions: list[Decimal] | None = None,
) -> str:
    if proportions:
        selected = "; ".join(
            f"{option['name']} : {format_decimal(proportion)} %"
            for option, proportion in zip(options, proportions)
        )
        minimum = minimum_order_volume(options)
        minimum_text = (
            f" Le volume minimum de commande pour ce mélange est de "
            f"{format_decimal(minimum)} kg."
            if minimum is not None
            else ""
        )
        return (
            f"Proportions retenues : {selected}.{minimum_text} "
            "Quel volume total souhaitez-vous commander pour ce mélange, "
            f"en kg ?\n{format_selected_options(options)}"
        )
    selected = ", ".join(str(option["name"]) for option in options)
    minimum = options[0].get("minimum_volume_kg") if options else None
    minimum_text = (
        f" Le minimum de commande pour cet arôme est de "
        f"{format_decimal(minimum)} kg."
        if minimum is not None
        else ""
    )
    return (
        f"Excellent choix ! Vous avez choisi {selected}. "
        f"{minimum_text} Quel volume souhaitez-vous commander, en kg ?\n"
        f"{format_selected_options(options)}"
    )


def format_order_mode_prompt(options: list[AROMA_OPTION]) -> str:
    return (
        f"Vous avez choisi {len(options)} arômes. Souhaitez-vous les "
        "commander séparément ou les mélanger ? Pour une commande séparée, "
        "le volume minimum s’applique à chaque arôme. Pour un mélange, "
        "indiquez les proportions, puis le volume total.\n"
        f"{format_selected_options(options)}"
    )


def format_separate_volumes_prompt(options: list[AROMA_OPTION]) -> str:
    labels = ", ".join(str(option["name"]) for option in options)
    example_volumes = [
        option.get("minimum_volume_kg") or Decimal("5")
        for option in options
    ]
    return (
        "Indiquez un volume en kg pour chacun des arômes, dans le même ordre "
        f"({labels}), par exemple : "
        f"{', '.join(f'{format_decimal(volume)} kg' for volume in example_volumes)}. "
        "Chaque volume doit atteindre le minimum indiqué.\n"
        f"{format_selected_options(options)}"
    )


def parse_order_volume(answer: str) -> Decimal | None:
    match = re.search(
        r"(?<!\w)(\d+(?:[.,]\d+)?)\s*(?:kg|kilogrammes?)?\b",
        normalize(answer),
    )
    if not match:
        return None
    try:
        volume = Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None
    return volume if volume > 0 else None


def parse_order_volumes(answer: str, expected_count: int) -> list[Decimal] | None:
    values = re.findall(
        r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:kg|kilogrammes?)\b",
        normalize(answer),
    )
    if len(values) != expected_count:
        return None
    try:
        volumes = [Decimal(value.replace(",", ".")) for value in values]
    except InvalidOperation:
        return None
    return volumes if all(volume > 0 for volume in volumes) else None


def minimum_order_volume(options: list[AROMA_OPTION]) -> Decimal | None:
    minimums = [
        minimum
        for option in options
        if (minimum := option.get("minimum_volume_kg")) is not None
    ]
    return min(minimums) if minimums else None


def handle_selection_followup(
    question: str,
    history: list[dict],
) -> dict | None:
    last_assistant = next(
        (
            item["content"]
            for item in reversed(history)
            if item.get("role") == "assistant"
            and isinstance(item.get("content"), str)
        ),
        "",
    )
    normalized_last = normalize(last_assistant)
    is_order_volume_prompt = (
        re.search(r"\bvolume\b.*\bcommander\b", normalized_last) is not None
    )
    is_separate_volumes_prompt = "pour chacun des aromes" in normalized_last
    previous_options = extract_selection_from_history(history)
    base_response = {
        "question": question,
        "model": config.LLM_MODEL,
        "sources": [],
        "suggestions": [],
    }

    if is_separate_volumes_prompt and previous_options:
        options = previous_options
        volumes = parse_order_volumes(question, len(options))
        if volumes is None:
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": (
                    "Indiquez un volume positif en kg pour chacun des "
                    f"{len(options)} arômes, dans le même ordre.\n"
                    f"{last_assistant}"
                ),
            }
        below_minimum = [
            (option, volume)
            for option, volume in zip(options, volumes)
            if option.get("minimum_volume_kg") is not None
            and volume < option["minimum_volume_kg"]
        ]
        if below_minimum:
            requirements = "; ".join(
                f"{option['name']} : au moins "
                f"{format_decimal(option['minimum_volume_kg'])} kg "
                f"(indiqué : {format_decimal(volume)} kg)"
                for option, volume in below_minimum
            )
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": (
                    f"Volume inférieur au minimum pour {requirements}. "
                    "Indiquez de nouveaux volumes respectant chaque minimum.\n"
                    f"{last_assistant}"
                ),
            }
        summary = "; ".join(
            f"{option['name']} : {format_decimal(volume)} kg"
            for option, volume in zip(options, volumes)
        )
        return {
            **base_response,
            "status": "recommendations_ready",
            "answer": f"Excellent choix ! Commandes séparées : {summary}.",
        }

    if "séparément ou" in normalized_last or "separement ou" in normalized_last:
        options = previous_options
        if "melange" in normalize(question):
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": format_proportions_prompt(options),
            }
        if any(word in normalize(question) for word in (
            "separe", "separement", "individuellement", "chacun",
        )):
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": format_separate_volumes_prompt(options),
            }
        return {
            **base_response,
            "status": "needs_clarification",
            "answer": (
                "Répondez « séparément » pour commander chaque arôme "
                "individuellement, ou « en mélange » pour indiquer des "
                "proportions.\n"
                f"{last_assistant}"
            ),
        }

    if is_order_volume_prompt and previous_options:
        options = previous_options
        proportion_message = next(
            (
                item["content"]
                for item in reversed(history)
                if item.get("role") == "assistant"
                and "proportions retenues" in normalize(item["content"])
            ),
            "",
        )
        proportions = parse_percentages(
            proportion_message,
            len(options),
        ) if proportion_message else None
        volume = parse_order_volume(question)
        if volume is None:
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": (
                    "Indiquez un volume positif à commander, en kg "
                    "(par exemple 10 kg).\n"
                    f"{last_assistant}"
                ),
            }
        minimum = (
            minimum_order_volume(options)
            if proportions
            else options[0].get("minimum_volume_kg")
        )
        if minimum is not None and volume < minimum:
            target = (
                "ce mélange"
                if proportions
                else f"l’arôme {options[0]['name']}"
            )
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": (
                    f"Le volume minimum de commande pour {target} est de "
                    f"{format_decimal(minimum)} kg. Indiquez un volume au "
                    f"moins égal à {format_decimal(minimum)} kg.\n"
                    f"{last_assistant}"
                ),
            }
        if proportions:
            selection_summary = "; ".join(
                f"{option['name']} : {format_decimal(proportion)} %"
                for option, proportion in zip(options, proportions)
            )
            answer = (
                f"Excellent choix ! Votre mélange est composé de "
                f"{selection_summary}, pour un volume total de "
                f"{format_decimal(volume)} kg."
            )
        else:
            selection_summary = ", ".join(
                str(option["name"]) for option in options
            )
            answer = (
                f"Excellent choix ! Votre sélection de {selection_summary} "
                f"est enregistrée pour un volume de {format_decimal(volume)} kg."
            )
        return {
            **base_response,
            "status": "recommendations_ready",
            "answer": answer,
        }

    if "proportion" in normalized_last and parse_aroma_options(last_assistant):
        options = parse_aroma_options(last_assistant)
        proportions = parse_percentages(question, len(options))
        if proportions is None:
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": (
                    "Je n’ai pas identifié une proportion pour chacun des "
                    "arômes. Indiquez un pourcentage par arôme, dans cet "
                    f"ordre : {', '.join(str(option['name']) for option in options)}. "
                    "La somme doit être égale à 100 %.\n"
                    f"{format_selected_options(options)}"
                ),
            }
        if sum(proportions) != Decimal("100"):
            return {
                **base_response,
                "status": "needs_clarification",
                "answer": format_proportions_prompt(options, proportions),
            }
        return {
            **base_response,
            "status": "needs_clarification",
            "answer": format_volume_prompt(options, proportions),
        }

    options = parse_aroma_options(last_assistant)
    if not options or "arome" not in normalized_last:
        return None

    selected = match_selected_options(question, options)
    if not selected:
        return {
            **base_response,
            "status": "needs_clarification",
            "answer": (
                "Indiquez le numéro ou le nom du ou des arômes que vous "
                "choisissez parmi la liste proposée."
            ),
        }
    if len(selected) > 1:
        return {
            **base_response,
            "status": "needs_clarification",
            "answer": format_order_mode_prompt(selected),
        }
    return {
        **base_response,
        "status": "needs_clarification",
        "answer": format_volume_prompt(selected),
    }


CLARIFICATION_QUESTIONS = {
    "application": (
        "Pour quel type de produit alimentaire recherchez-vous un arôme ? Par exemple "
        "une boisson, un dessert, une confiserie, un produit laitier, un "
        "biscuit ou un plat préparé."
    ),
    "family": (
        "Quel univers aromatique recherchez-vous ? Par exemple agrumes, "
        "fruits rouges, fruits tropicaux, herbes et épices, notes gourmandes "
        "(vanille, caramel, chocolat) ou notes salées."
    ),
    "sweetness": (
        "Quel niveau de sucrosité souhaitez-vous, de 1 à 5 ? "
        "1 correspond à peu sucré, 3 à équilibré et 5 à très sucré. "
        "Vous pouvez aussi répondre « peu importe »."
    ),
    "heat": (
        "Le produit subira-t-il une cuisson ou une pasteurisation ? Vous "
        "pouvez répondre oui, non ou « je ne sais pas » ; si vous n’avez pas "
        "de contrainte de chaleur, dites-le."
    ),
    "certifications": (
        "Avez-vous besoin d’une certification particulière, par exemple "
        "bio, halal, kasher ou vegan, ou aucune exigence ?"
    ),
}

QUALIFICATION_SLOTS = (
    "application",
    "family",
    "sweetness",
    "heat",
    "certifications",
)


def get_missing_qualification(
    question: str,
    history: list[dict],
    conversation: str,
) -> str | None:
    missing_slots = get_missing_qualifications(
        question,
        history,
        conversation,
    )
    return missing_slots[0] if missing_slots else None


def get_missing_qualifications(
    question: str,
    history: list[dict],
    conversation: str,
) -> list[str]:
    missing = []
    for slot in QUALIFICATION_SLOTS:
        if get_missing_qualification_for_slot(
            slot,
            question,
            history,
            conversation,
        ):
            missing.append(slot)
    return missing


def get_missing_qualification_for_slot(
    slot: str,
    question: str,
    history: list[dict],
    conversation: str,
) -> bool:
    conversation_messages = [
        item
        for item in history[-HISTORY_LIMIT:]
        if isinstance(item, dict)
    ] + [{"role": "user", "content": question}]
    answered_without_requirement = any(
        item.get("role") == "assistant"
        and index + 1 < len(conversation_messages)
        and conversation_messages[index + 1].get("role") == "user"
        and get_question_slot(str(item.get("content", ""))) == slot
        and is_no_requirement(
            str(conversation_messages[index + 1].get("content", ""))
        )
        for index, item in enumerate(conversation_messages[:-1])
    )
    detected = {
        "application": bool(detect_application(conversation)),
        "family": bool(detect_family(conversation)),
        "sweetness": bool(
            detect_sweetness_filter(conversation)
            or any(
                item["column"] == "sucrosite"
                for item in detect_numeric_filters(conversation)
            )
        ),
        "heat": bool(detect_heat_filter(conversation)),
        "certifications": bool(detect_certifications(conversation)),
    }
    return not detected[slot] and not answered_without_requirement


def choose_next_qualification(
    question: str,
    history: list[dict],
    conversation: str,
    missing_slots: list[str],
) -> str:
    response_schema = {
        "type": "object",
        "properties": {
            "next_field": {
                "type": "string",
                "enum": missing_slots,
            },
        },
        "required": ["next_field"],
        "additionalProperties": False,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Tu mènes un entretien de qualification pour comprendre le "
                "besoin du client avant toute proposition d'arôme. À partir "
                "de la demande, de l'historique et des critères manquants, "
                "choisis le critère manquant le plus utile à clarifier. "
                "Ne répète jamais un critère déjà renseigné ou refusé. "
                "Ne recommande aucun arôme. Réponds uniquement avec "
                "next_field parmi les valeurs autorisées."
            ),
        },
    ]
    messages.extend(
        {
            "role": item["role"],
            "content": item["content"][-4000:],
        }
        for item in history[-HISTORY_LIMIT:]
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
    )
    messages.append({
        "role": "user",
        "content": (
            f"Réponse la plus récente : {question}\n"
            f"Demande cumulée du client : {conversation}\n"
            f"Critères encore manquants : {json.dumps(missing_slots)}\n"
            "Choisis le prochain critère à demander."
        ),
    })

    try:
        response = rag.chat(messages, response_format=response_schema)
        start = response.find("{")
        end = response.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"JSON absent. Réponse reçue : {response}")
        result = json.loads(response[start:end + 1])
        next_field = result.get("next_field")
        if next_field not in missing_slots:
            raise ValueError(
                f"Critère de qualification invalide : {next_field}"
            )
        return next_field
    except Exception as exc:
        logger.exception("[ERREUR QWEN] Échec du choix du prochain critère")
        raise HTTPException(
            status_code=502,
            detail="Le modèle Qwen n'a pas pu choisir le prochain critère.",
        ) from exc


def generate_client_guidance(
    question: str,
    history: list[dict],
    rows: list[dict],
    family: str | None,
    application: str | None,
    numeric_filters: list[dict],
    heat_filter: str | None,
    certification_filters: list[str],
) -> dict:
    candidate_ids = [
        str(row["aroma_id"])
        for row in rows
    ]
    compatible_families = sorted({
        str(row["famille"])
        for row in rows
        if row.get("famille")
    })
    catalogue = [
        {
            "aroma_id": row["aroma_id"],
            "nom": row["nom"],
            "famille": row["famille"],
            "notes": row["notes"],
            "applications": row["applications"],
            "sucrosite": row["sucrosite"],
            "tenue_chaleur": row["tenue_chaleur"],
            "certifications": row["certifications"],
            "prix_indicatif_eur_kg": row["prix_indicatif_eur_kg"],
            "delai_jours": row["delai_jours"],
            "volume_minimum_kg": row["volume_minimum_kg"],
            "is_synthetic": row["is_synthetic"],
            "source_information": row["source_information"],
        }
        for row in rows
    ]
    constraints = {
        "famille": family,
        "application": application,
        "filtres_numeriques": numeric_filters,
        "tenue_chaleur": heat_filter,
        "certifications": certification_filters,
    }
    response_schema = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["needs_clarification", "recommendations_ready"],
            },
            "clarification": {
                "type": "string",
                "enum": [*CLARIFICATION_QUESTIONS, "none"],
            },
            "recommended_ids": {
                "type": "array",
                "items": (
                    {"type": "string", "enum": candidate_ids}
                    if candidate_ids
                    else {"type": "string"}
                ),
            },
        },
        "required": ["status", "clarification", "recommended_ids"],
        "additionalProperties": False,
    }

    messages = [
        {
            "role": "system",
            "content": (
                "Tu es Qwen, conseiller aromatique conversationnel "
                "d'Umi. Échange avec le client en français, "
                "en tenant compte de tout l'historique fourni.\n\n"
                "Les seules familles du catalogue sont : Agrumes, "
                "Fruits rouges, Fruits tropicaux, Herbes et épices, "
                "Notes gourmandes, Notes salées.\n"
                "Règles :\n"
                "1. Utilise uniquement les faits des résultats du catalogue "
                "fournis dans le dernier message.\n"
                "2. Ne recommande que les arômes présents dans ces résultats "
                "et les familles compatibles indiquées.\n"
                "3. N'invente ni prix, ni délai, ni certification, ni "
                "caractéristique. Une valeur null ou une liste vide signifie "
                "que l'information n'est pas renseignée.\n"
                "4. Respecte les filtres SQL appliqués et n'élargis pas les "
                "résultats au-delà de ces critères.\n"
                "5. Les scores cosinus servent uniquement au classement et "
                "ne doivent jamais apparaître dans ta réponse.\n"
                "6. Si une précision est nécessaire, choisis une valeur "
                "clarification adaptée, en tenant compte de tout l'historique "
                "et sans redemander une information connue.\n"
                "7. Si tu recommandes, choisis uniquement les IDs exacts "
                "des candidats dans recommended_ids; l'API rédigera les "
                "faits vérifiés du catalogue.\n"
                "8. Si tu demandes une précision, utilise "
                "needs_clarification et laisse recommended_ids vide.\n"
                "9. Si tu recommandes, utilise recommendations_ready et "
                "clarification=none.\n"
                "10. Ne présente jamais une suggestion comme une formule "
                "industrielle validée.\n\n"
                "Retourne seulement status, clarification et "
                "recommended_ids. Ne rédige aucune réponse libre."
            ),
        }
    ]

    for item in history[-12:]:
        if (
            isinstance(item, dict)
            and item.get("role") in ("user", "assistant")
            and isinstance(item.get("content"), str)
        ):
            messages.append({
                "role": item["role"],
                "content": item["content"][-4000:],
            })

    messages.append({
        "role": "user",
        "content": (
            f"Demande actuelle du client : {question}\n\n"
            "Contraintes détectées et appliquées en base :\n"
            f"{json.dumps(constraints, ensure_ascii=False, default=str)}\n\n"
            "Familles compatibles présentes dans les résultats :\n"
            f"{json.dumps(compatible_families, ensure_ascii=False)}\n\n"
            "Candidats SQL classés par similarité cosinus (seuls arômes "
            "autorisés dans recommended_ids) :\n"
            f"{json.dumps(catalogue, ensure_ascii=False, default=str)}\n\n"
            "Continue naturellement la conversation. Si une précision est "
            "nécessaire, choisis une seule catégorie de question sans répéter "
            "l'historique. Sinon, choisis les IDs utiles de cette liste."
        ),
    })

    try:
        logger.info("[QWEN] Appel du modèle %s", config.LLM_MODEL)

        response = rag.chat(
            messages,
            response_format=response_schema,
        )

        logger.info("[QWEN] Réponse reçue.")

        start = response.find("{")
        end = response.rfind("}")

        if start == -1 or end == -1:
            raise ValueError(
                f"JSON absent. Réponse reçue : {response}"
            )

        result = json.loads(response[start:end + 1])

        status = result.get("status")
        clarification = result.get("clarification")
        recommended_ids = result.get("recommended_ids")
        if status not in {"needs_clarification", "recommendations_ready"}:
            raise ValueError(f"Statut invalide : {status}")
        if clarification not in {*CLARIFICATION_QUESTIONS, "none"}:
            raise ValueError("Type de clarification invalide")
        if (
            not isinstance(recommended_ids, list)
            or any(not isinstance(item, str) for item in recommended_ids)
        ):
            raise ValueError("Liste d'identifiants recommandés invalide")

        if status == "needs_clarification":
            if clarification == "none":
                raise ValueError("Question de clarification absente")
            return {
                "status": status,
                "answer": CLARIFICATION_QUESTIONS[clarification],
            }

        if clarification != "none":
            raise ValueError(
                "Une recommandation ne doit pas contenir de clarification."
            )
        if not rows or not recommended_ids:
            raise ValueError(
                "Le modèle recommande des arômes sans identifiant candidat."
            )

        rows_by_id = {str(row["aroma_id"]): row for row in rows}
        unknown_ids = set(recommended_ids) - rows_by_id.keys()
        if unknown_ids:
            raise ValueError(
                "Le modèle a sélectionné des arômes absents des résultats SQL."
            )

        recommended_id_set = set(recommended_ids)
        selected_rows = [
            rows_by_id[source_id]
            for source_id in candidate_ids
            if source_id in recommended_id_set
        ]

        return {
            "status": "recommendations_ready",
            "answer": format_recommendation_answer(
                selected_rows,
                numeric_filters,
                heat_filter,
            ),
            "recommended_ids": [str(row["aroma_id"]) for row in selected_rows],
        }

    except Exception as exc:
        logger.exception("[ERREUR QWEN] Échec de la génération")

        raise HTTPException(
            status_code=502,
            detail="Le modèle Qwen n'a pas pu traiter la demande.",
        ) from exc


# --------------------------------------------------
# CONSTRUCTION DES SOURCES
# --------------------------------------------------

def display_catalogue_value(value: object) -> str:
    if value is None or value == []:
        return "non renseigné"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def build_source(row: dict) -> AromaSource:
    text = (
        f"Arôme : {row['nom']}. "
        f"Famille : {row['famille']}. "
        f"Notes : {display_catalogue_value(row['notes'])}. "
        f"Applications : {display_catalogue_value(row['applications'])}. "
        f"Sucrosité : {display_catalogue_value(row['sucrosite'])}. "
        "Tenue à la chaleur : "
        f"{display_catalogue_value(row['tenue_chaleur'])}. "
        f"Certifications : {display_catalogue_value(row['certifications'])}. "
        "Prix indicatif : "
        f"{display_catalogue_value(row['prix_indicatif_eur_kg'])} euros/kg. "
        f"Délai : {display_catalogue_value(row['delai_jours'])} jours. "
        "Volume minimum : "
        f"{display_catalogue_value(row['volume_minimum_kg'])} kg. "
        f"Source : {display_catalogue_value(row['source_information'])}."
    )

    return AromaSource(
        source_type="arome",
        source_id=str(row["aroma_id"]),
        is_synthetic=bool(row["is_synthetic"]),
        text=text,
        score=row["_similarity_score"],
        nom=row["nom"],
        famille=row["famille"],
        notes=row["notes"],
        applications=row["applications"] or [],
        sucrosite=row["sucrosite"],
        tenue_chaleur=row["tenue_chaleur"],
        prix=row["prix_indicatif_eur_kg"],
        delai=row["delai_jours"],
    )


# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.get("/health", tags=["Système"])
def health():
    try:
        count = rag.get_collection().count()
    except Exception:
        logger.exception("Impossible de vérifier ChromaDB")
        count = None

    return {
        "status": "online",
        "service": "Umi",
        "documents_indexed": count,
        "llm_model": config.LLM_MODEL,
        "embed_model": config.EMBED_MODEL,
    }


@app.get("/aromes", tags=["Catalogue aromatique"])
def get_aromes(
    famille: str | None = None,
    application: str | None = None,
):
    conn = None

    try:
        conn = get_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = "SELECT * FROM public.food_aromas WHERE 1=1"
            params = []

            if famille:
                query += " AND famille ILIKE %s"
                params.append(f"%{famille}%")

            if application:
                query += """
                    AND EXISTS (
                        SELECT 1
                        FROM unnest(applications) AS app
                        WHERE app ILIKE %s
                    )
                """
                params.append(f"%{application}%")

            query += " ORDER BY nom"

            cursor.execute(query, params)

            return [dict(row) for row in cursor.fetchall()]

    except Exception as exc:
        logger.exception("Impossible de lire le catalogue")

        raise HTTPException(
            status_code=503,
            detail="Impossible de lire le catalogue.",
        ) from exc

    finally:
        if conn is not None:
            conn.close()


@app.post(
    "/search",
    response_model=list[AromaSource],
    tags=["RAG aromatique"],
)
def search(req: SearchRequest):
    try:
        documents = rag.search_documents(
            req.question,
            req.top_k,
        )

        return [
            AromaSource(
                source_type=document["metadata"]["source_type"],
                source_id=str(document["metadata"]["source_id"]),
                is_synthetic=(
                    str(document["metadata"]["is_synthetic"]).lower()
                    == "true"
                ),
                text=document["text"],
                score=document["score"],
            )
            for document in documents
        ]

    except Exception as exc:
        logger.exception("Erreur pendant la recherche ChromaDB")

        raise HTTPException(
            status_code=503,
            detail="Erreur lors de la recherche dans ChromaDB.",
        ) from exc


@app.post(
    "/ask",
    response_model=AskResponse,
    tags=["Assistant conversationnel"],
)
def ask(req: SearchRequest):
    history = [item.model_dump() for item in req.history]
    selection_response = handle_selection_followup(
        req.question,
        history,
    )
    if selection_response is not None:
        return selection_response

    conversation = get_conversation_text(
        req.question,
        history,
    )

    missing_qualifications = get_missing_qualifications(
        req.question,
        history,
        conversation,
    )
    required_missing = [
        slot
        for slot, is_known in (
            ("family", detect_family(conversation) is not None),
            ("application", detect_application(conversation) is not None),
        )
        if not is_known
    ]
    missing_qualifications = list(dict.fromkeys(
        [*missing_qualifications, *required_missing]
    ))
    if missing_qualifications:
        next_field = choose_next_qualification(
            req.question,
            history,
            conversation,
            missing_qualifications,
        )
        return {
            "question": req.question,
            "status": "needs_clarification",
            "answer": CLARIFICATION_QUESTIONS[next_field],
            "model": config.LLM_MODEL,
            "sources": [],
            "suggestions": [],
        }

    try:
        semantic_documents = rag.search_aromas(conversation)
    except Exception as exc:
        logger.exception("Erreur pendant la recherche vectorielle des arômes")
        raise HTTPException(
            status_code=503,
            detail="Erreur pendant la recherche sémantique.",
        ) from exc

    (
        rows,
        family,
        application,
        numeric_filters,
        heat_filter,
        certification_filters,
    ) = search_catalogue(
        conversation,
        semantic_documents,
        req.top_k,
    )

    no_exact_match = not rows
    if no_exact_match:
        (
            rows,
            _,
            _,
            _,
            _,
            _,
        ) = search_catalogue(
            conversation,
            semantic_documents,
            req.top_k,
            ignored_criteria={"all"},
        )

        if not rows:
            return {
                "question": req.question,
                "status": "needs_clarification",
                "answer": (
                    "Aucun arôme n’est disponible dans le catalogue pour "
                    "correspondre à cette demande."
                ),
                "model": config.LLM_MODEL,
                "sources": [],
                "suggestions": [],
            }

        answer = format_recommendation_answer(
            rows,
            [],
            None,
            introduction=(
                "Je n’ai pas trouvé d’arôme respectant tous vos critères. "
                "Voici les options les plus proches de votre demande ; "
                "vérifiez leurs caractéristiques, qui peuvent différer "
                "de vos contraintes :"
            ),
        )
        recommended_rows = rows
        response_status = "recommendations_ready"
    else:
        response = generate_client_guidance(
            req.question,
            history,
            rows,
            family,
            application,
            numeric_filters,
            heat_filter,
            certification_filters,
        )
        recommended_ids = set(response.get("recommended_ids", []))
        recommended_rows = [
            row for row in rows
            if str(row["aroma_id"]) in recommended_ids
        ]
        answer = response["answer"]
        response_status = response["status"]

    sources = [
        build_source(row)
        for row in recommended_rows
    ]

    return {
        "question": req.question,
        "status": response_status,
        "answer": answer,
        "model": config.LLM_MODEL,
        "sources": [
            source.model_dump()
            for source in sources
        ],
        "suggestions": [
            str(row["nom"])
            for row in recommended_rows
        ],
    }


# --------------------------------------------------
# LANCEMENT
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host="127.0.0.1",
        port=8001,
        reload=True,
    )
