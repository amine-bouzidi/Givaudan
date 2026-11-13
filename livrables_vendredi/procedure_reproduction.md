# Procédure de reproduction de la comparaison sans RAG / avec RAG

Cette procédure compare Qwen utilisé seul aux réponses HTTP de l'API Umi avec
RAG, sans utiliser les métriques internes de recherche comme substitut aux
sorties visibles. Les commandes sont prévues pour PowerShell, depuis la racine
du dépôt.

## 1. Prérequis

- Python 3.10 ou plus récent ;
- PostgreSQL contenant `public.food_aromas` ;
- Ollama installé et démarré localement ;
- les modèles `nomic-embed-text` et `qwen2.5:1.5b`.

Créer/restaurer la table `public.food_aromas` depuis
`data/food_aromas.sql`. Configurer `.env` avec les variables PostgreSQL
attendues par `database.py` et ne pas committer ce fichier.

## 2. Installer et indexer

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull nomic-embed-text
ollama pull qwen2.5:1.5b
.\.venv\Scripts\python.exe ingest.py
```

Le script d'indexation synchronise le catalogue et les documents de
connaissance dans ChromaDB. PostgreSQL et Ollama doivent rester accessibles
pendant l'évaluation.

## 3. Démarrer l'API

Dans un premier terminal PowerShell :

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8001
```

Vérifier que l'API répond :

```powershell
Invoke-RestMethod http://127.0.0.1:8001/openapi.json |
    Select-Object -ExpandProperty info
```

## 4. Appeler les endpoints et produire les artefacts

Dans un second terminal, à la racine du dépôt :

```powershell
.\.venv\Scripts\python.exe .\livrables_vendredi\evaluer_api_ask.py
```

Le script lit le catalogue par `GET /aromes`, la version par
`GET /openapi.json`, puis envoie les 30 cas de référence par `POST /ask`. Il
utilise un historique de qualification identique pour chaque cas afin que
l'API puisse renvoyer les recommandations sans demander d'abord des critères
supplémentaires.

Les fichiers suivants sont créés dans `livrables_vendredi/` :

- `catalogue_api.json` : réponse brute de `GET /aromes` ;
- `resultats_api_ask.json` : réponse complète de `/ask` pour chaque cas ;
- `resultats_api_ask.csv` : version tabulaire pour consultation ;
- `metriques_api_ask.json` : résumé des réponses API et de leur latence.

Le script échoue explicitement si une requête API échoue. Il ne modifie pas les
résultats du rapport du jeudi.

## 5. Évaluer le modèle sans RAG

Après avoir produit `catalogue_api.json`, lancer :

```powershell
.\.venv\Scripts\python.exe .\livrables_vendredi\evaluer_modele_sans_rag.py
```

Le script envoie les mêmes questions et le même historique au modèle Qwen,
mais sans fournir de catalogue ni de documents récupérés. Il utilise
`catalogue_api.json` uniquement après la génération pour comparer le nom de la
cible attendue au texte produit. Les réponses et mesures sont enregistrées
dans `resultats_sans_rag.json` et `metriques_sans_rag.json`.

## 6. Métriques à rapporter

Comparer les métriques du modèle seul dans `metriques_sans_rag.json` avec les
sorties finales de l'API dans `metriques_api_ask.json` :

- pour le modèle sans RAG, le taux de réponses contenant le libellé exact de la
  cible attendue ;
- pour l'API avec RAG, la présence de la cible dans les sources, le respect des
  critères par les sources et les statuts ;
- la latence de chaque configuration, en indiquant que le sans-RAG mesure
  l'appel modèle seul tandis que le avec-RAG mesure le parcours HTTP complet.

## 7. Résultats de l'exécution archivée

L'exécution locale archivée le 13 novembre 2026 a utilisé l'API 6.3.0 et
Qwen `qwen2.5:1.5b`. Les résultats détaillés sont dans les deux fichiers de
métriques ci-dessus.

- `GET /aromes` a retourné 201 entrées, toutes avec `is_synthetic: true` ;
- sans RAG, le libellé exact de la cible est apparu dans 0/30 réponses ;
- le modèle seul a mesuré 5,99 s en moyenne, 5,26 s en médiane et 11,58 s au
  p95 ;
- les 30 appels `/ask` ont retourné `recommendations_ready` ;
- avec RAG, le nom exact de la cible est apparu dans 29/30 réponses ;
- la cible attendue figurait parmi les sources dans 29 réponses sur 30 ;
- les 90 sources retournées respectaient les critères annotés et étaient
  marquées synthétiques ;
- latence HTTP `/ask` : moyenne 31,91 s, médiane 33,02 s, p95 43,63 s.

La latence sans RAG mesure l'appel au modèle seul ; celle de `/ask` mesure
l'API complète. Les deux valeurs sont donc présentées avec leur périmètre et
ne constituent pas une comparaison directe de performance.
