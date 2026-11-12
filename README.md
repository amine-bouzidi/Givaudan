# Umi API

API conversationnelle de recommandation d'arômes alimentaires. PostgreSQL
contient le catalogue `public.food_aromas`, ChromaDB classe les arômes par
similarité cosinus et Ollama (Qwen par défaut) répond au client à partir des
résultats filtrés.

## Prérequis

- Python 3.10+
- PostgreSQL avec la table `public.food_aromas`
- Ollama lancé localement avec les modèles configurés

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull nomic-embed-text
ollama pull qwen2.5:1.5b
```

Configurer les variables PostgreSQL dans un fichier `.env` à la racine du
projet :

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=olfactive_studio
DB_USER=postgres
DB_PASSWORD=change-me
```

Ne pas committer `.env`. `OLLAMA_HOST`, `LLM_MODEL`, `EMBED_MODEL`,
`CHROMA_DIR`, `COLLECTION_NAME` et `TOP_K` sont aussi configurables par
variables d'environnement.

## Indexation sémantique

Les enregistrements de `public.food_aromas` doivent être indexés dans ChromaDB
avant d'utiliser `/ask` ou `/search` :

```powershell
.\.venv\Scripts\python.exe ingest.py
```

Le script génère les embeddings des arômes issus de PostgreSQL, actualise les
identifiants déjà indexés et retire de ChromaDB les anciens arômes supprimés
du catalogue. Il ajoute également les fichiers `.txt` de `data/knowledge`.
Après un changement du modèle d'embeddings, supprimer puis reconstruire la
collection avec `clean.py` et `ingest.py`.

## Lancer l'API

```powershell
.\.venv\Scripts\python.exe api.py
```

- Swagger : <http://127.0.0.1:8001/docs>
- OpenAPI : <http://127.0.0.1:8001/openapi.json>

## Conversation depuis PowerShell

Dans un second terminal PowerShell, depuis la racine du projet, lancer :

```powershell
.\conversation.ps1
```

Le client peut commencer par n'importe quel critère. Qwen choisit ensuite un
critère encore inconnu, et l'assistant propose des exemples de réponses pour
aider à préciser la demande. Écrire `quit` pour fermer la conversation.

## Qualification de la demande

`POST /ask` :

1. Identifie les critères déjà exprimés, quel que soit l'ordre dans lequel le
   client les donne : application, famille aromatique, sucrosité, tenue à la
   chaleur et certifications.
2. Demande à Qwen de choisir le critère encore inconnu le plus utile, en
   tenant compte de l'historique. La question propose des exemples de réponses.
   Le client peut répondre qu'il ne sait pas ou qu'il n'a pas d'exigence.
3. La famille aromatique et l'application sont obligatoires avant toute
   recommandation. Les autres critères sont également clarifiés avant la
   recherche de produits.
4. Une fois la demande qualifiée, calcule l'embedding et classe les candidats
   par similarité cosinus dans ChromaDB.
5. Applique les contraintes prises en charge dans PostgreSQL et demande à
   Qwen de sélectionner uniquement parmi les candidats filtrés.
6. Retourne `status`, `answer`, les sources et des `suggestions` pour les
   interactions du front-end. Chaque source d'arôme contient aussi des champs
   dédiés (`nom`, `famille`, `notes`, `applications`, `sucrosite`,
   `tenue_chaleur`, `prix`, `delai`) en plus du texte descriptif.

La détection distingue les familles des applications : « citron » identifie
la famille Agrumes, tandis que « biscuit » identifie l'application biscuits.

Si aucun arôme ne respecte tous les filtres, l'API reprend les candidats
sémantiques, les classe par similarité et présente les plus proches de la
description en indiquant qu'aucune correspondance exacte n'a été trouvée et
que les caractéristiques des alternatives peuvent différer des contraintes.
Le client peut ensuite choisir un arôme par son numéro ou son nom, ou plusieurs
arômes puis choisir de les commander séparément ou en mélange. En commande
séparée, le volume indiqué pour chaque arôme doit respecter son minimum de
commande propre. En mélange de N arômes, l'assistant demande N proportions,
vérifie que leur somme est 100 %, puis exige un volume total au moins égal au
plus petit minimum de commande des arômes du mélange. Pour un arôme unique,
le volume demandé doit respecter son minimum de commande. Après réception des
volumes, l'assistant confirme le choix.

La conversation est sans état côté serveur : le client transmet à chaque
requête `question` et l'historique précédent dans `history` (24 messages
maximum). Après la réponse, il ajoute la question du client et `answer` comme
messages `user` et `assistant`, conserve les 24 messages les plus récents, puis
transmet cet historique à la requête suivante.

Exemple :

```json
{
  "question": "Je cherche quelque chose pour une boisson pasteurisée.",
  "top_k": 4,
  "history": []
}
```

`POST /search` fournit les résultats vectoriels sans génération de réponse.
`GET /aromes` lit le catalogue PostgreSQL et accepte les paramètres facultatifs
`famille` et `application`. `GET /health` indique la configuration et l'état
de l'index ChromaDB.

Toutes les données de démonstration marquées `is_synthetic=true` sont
fictives. Elles ne constituent ni des références commerciales vérifiées ni
des recommandations de formulation industrielle.
