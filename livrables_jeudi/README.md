# Livrables Data — Umi

Document de synthèse et contenu prêt à reprendre dans la présentation du
prototype. Les fichiers d'évaluation détaillés sont dans ce dossier.

## État des livrables

| Livrable attendu | État observé | Preuve |
|---|---|---|
| Données préparées | Disponible : catalogue de démonstration `food_aromas`, 201 lignes | `../data/food_aromas.sql` |
| Pipeline d'indexation | Fonctionnel localement, PostgreSQL vers ChromaDB via Ollama | `../ingest.py` |
| Modèle V2 publié | Non démontré comme publié : les modèles sont configurés et utilisés en local ; aucun modèle fine-tuné ni endpoint distant n'est livré ici | `../config.py`, `/health`, configuration Ollama |
| Protocole et métriques sur 30 cas | Exécutés pour la détection, le retrieval et le filtrage SQL ; le parcours Qwen complet n'a pas été évalué sur les 30 cas | `cas_reference_30.jsonl`, `evaluer_30_cas.py`, `metriques_30.json` |
| API fonctionnelle | Testée localement : une recommandation complète a été obtenue via `/ask` | `../api.py`, tests ci-dessous |

Ne pas présenter le modèle comme « publié » ou déployé à l'extérieur tant qu'un
artefact/version hébergé et son endpoint n'ont pas été fournis et testés.

## Provenance et limites des données

Selon le porteur du projet, les données synthétiques ont été construites à
partir d'un brief réel. Le brief source n'est pas inclus dans le dépôt et cette
provenance n'a donc pas été vérifiée indépendamment. Le catalogue ne constitue
pas une base de références produits réelle.

Les 201 lignes PostgreSQL sont marquées `is_synthetic=true`. Les noms et
profils servent à tester l'expérience de recommandation. Les prix, délais,
certifications et volumes minimums sont des valeurs fictives, non vérifiées et
non contractuelles. Ne pas les utiliser pour une offre, une formulation ou une
décision d'achat. Les identifiants `AR-*` ne sont pas des SKU authentifiés.

## Données préparées et contrôles

La table est `public.food_aromas` et contient les champs suivants :

| Champ | Usage |
|---|---|
| `aroma_id`, `nom` | Identifiant stable et libellé de démonstration |
| `famille`, `notes` | Classification aromatique et description textuelle |
| `applications` | Liste PostgreSQL `text[]` des applications compatibles |
| `sucrosite` | Échelle de 1 à 5 |
| `tenue_chaleur` | `faible`, `moyenne` ou `élevée` |
| `certifications` | Liste de certifications ; vide signifie non renseigné |
| `prix_indicatif_eur_kg`, `delai_jours`, `volume_minimum_kg` | Champs commerciaux fictifs de démonstration |
| `is_synthetic`, `source_information` | Indication de provenance et avertissement associé |

Contrôles observés sur la base locale :

- 201 lignes, 201 identifiants distincts ; les 201 sont synthétiques.
- Six familles : Agrumes (25), Fruits rouges (36), Fruits tropicaux (30),
  Herbes et épices (35), Notes gourmandes (35), Notes salées (40).
- Aucun doublon de variante selon le regroupement `nom + famille +
  applications`.
- Les champs `nom`, `famille`, `notes`, `applications`, `sucrosite` et
  `tenue_chaleur` sont renseignés ; la sucrosité respecte l'échelle 1–5.
- `tenue_chaleur` contient 76 valeurs `élevée`, 100 `moyenne` et 25 `faible`.
- Certifications : liste vide pour les 201 lignes ; aucune certification
  commerciale n'est attestée par ce jeu.
- Une seule ligne de démonstration, `AR-demo-fruits-rouges-boisson-pasteurisee`,
  n'a pas de prix, de délai ni de volume minimum (200 lignes sur 201 ont ces
  trois champs renseignés).
- Plages des valeurs présentes : prix 41,24–158,84 €/kg, délai 10–30 jours,
  volume minimum 5–25 kg ; toutes ces valeurs sont fictives.

La collection ChromaDB locale `aromes` contient 208 documents : 201 arômes et
7 documents de connaissance. La recherche d'arômes interroge les documents
catalogue.

## Fonctionnement du pipeline

1. `data/food_aromas.sql` alimente PostgreSQL.
2. `ingest.py` lit les arômes, forme un texte descriptif, calcule les vecteurs
   avec Ollama (`nomic-embed-text`) et les synchronise dans ChromaDB.
3. `/ask` recueille les critères nécessaires, notamment famille et application.
   ChromaDB classe les candidats ; PostgreSQL applique les filtres métier et
   l'ordre de pertinence.
4. Ollama (`qwen2.5:1.5b`) formule une question de clarification ou présente les
   recommandations sélectionnées. La réponse expose aussi les champs utiles au
   front-end et les suggestions.

Les noms de modèles correspondent à la configuration locale observée, pas à un
entraînement ou à une publication de modèle.

## Protocole et résultats sur 30 cas

Le jeu `cas_reference_30.jsonl` comprend cinq cas par famille aromatique. Les
attentes annotées sont la famille, l'application, la sucrosité, la tenue à la
chaleur lorsqu'elle est demandée et une cible du catalogue.

Le script `evaluer_30_cas.py` vérifie d'abord que les cibles et leurs
annotations existent dans PostgreSQL. Il mesure ensuite :

- la détection de famille et d'application ;
- le rang brut de la cible dans les quatre premiers résultats vectoriels
  (`Hit@4`, `MRR@4`) ;
- la présence de la cible parmi les quatre premiers résultats après filtres
  SQL, ainsi que le respect des contraintes par toutes les lignes retournées ;
- la latence locale d'embedding, retrieval et filtrage SQL.

Résultats de la dernière exécution (`metriques_30.json`) :

| Mesure | Résultat |
|---|---:|
| Cas | 30 |
| Détection de famille | 100 % (30/30) |
| Détection d'application | 100 % (30/30) |
| Cible dans le top 4 vectoriel brut | 56,7 % (17/30) |
| MRR vectoriel top 4 | 0,344 |
| Cible dans le top 4 après filtres SQL | 100 % (30/30) |
| Conformité des résultats SQL aux contraintes | 100 % |
| Latence embedding + retrieval + filtre SQL — moyenne / médiane / p95 | 389,60 / 375,02 / 543,44 ms |

Ces résultats sont ceux d'un test d'intégration/régression sur un catalogue
synthétique. Les requêtes et leurs cibles proviennent du même catalogue ; le
jeu n'est ni aveugle ni indépendant, et ces mesures ne valident pas la qualité
métier. Le `Hit@4` vectoriel brut plus faible est compensé ici par le filtrage
et le classement SQL ; ce résultat ne doit pas être interprété comme une
évaluation générale de l'embedding.

Le script n'a pas exécuté Qwen sur les 30 cas (`end_to_end_run=false`). Une
exécution réelle de `/ask` a toutefois été faite avec l'historique de
qualification complet : statut `recommendations_ready`, modèle
`qwen2.5:1.5b`, réponse `Citron - boissons` et `Lime - boissons` pour la
demande testée, latence observée 2,30 s. C'est un seul essai local, pas une
métrique de latence stable ni une validation end-to-end sur 30 cas.

## API et validation locale

Un essai complet de `/ask` avec famille, application, sucrosité et historique
de qualification a renvoyé deux sources structurées :

- `AR-citron-boissons` — Agrumes, application `boissons` ;
- `AR-lime-boissons` — Agrumes, application `boissons`.

Les deux étaient marquées synthétiques. La réponse comprenait aussi
`recommendations_ready` et les suggestions de produits. L'API est donc testée
en local ; cela ne prouve pas qu'elle est déployée ou accessible depuis une
plateforme distante.

Commandes de reproduction, depuis la racine du dépôt sous PowerShell :

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe livrables_jeudi\evaluer_30_cas.py
```

Le second script dépend de PostgreSQL, ChromaDB et d'Ollama déjà configurés et
en fonctionnement. L'option `--end-to-end` exécute aussi `/ask` avec Qwen pour
chacun des 30 cas et peut être longue ; les chiffres présentés ici ne viennent
pas de cette option.

## Texte prêt à mettre dans les slides

### Slide — Données préparées

**Titre :** Un catalogue synthétique structuré pour prototyper la recommandation

**À afficher :**

- 201 arômes de démonstration, tous identifiés comme synthétiques.
- 6 familles aromatiques, 13 applications, critères de sucrosité et tenue à la
  chaleur.
- Schéma PostgreSQL `food_aromas`, avec filtres et données structurées pour
  l'interface.
- Selon le porteur du projet, le jeu a été construit à partir d'un brief réel ;
  il ne représente pas un catalogue commercial authentifié.
- Prix, délais, certifications et minima de commande : valeurs fictives à
  remplacer/valider avant tout usage opérationnel.

### Slide — Pipeline Data / RAG

**Titre :** Du catalogue aux recommandations conversationnelles

**À afficher comme schéma :**

`food_aromas.sql → PostgreSQL → ingest.py + nomic-embed-text → ChromaDB → filtres SQL → Qwen → API /ask`

**À dire :** PostgreSQL reste la source structurée ; ChromaDB classe les
candidats sémantiquement ; les contraintes exactes sont appliquées en SQL ;
Qwen gère la clarification et la formulation de la réponse.

### Slide — Modèle et API V2

**Titre :** Prototype conversationnel exposé par API

**À afficher :**

- API Umi version 6.3.0, route `/ask`, sources structurées et suggestions.
- Modèle local de génération : `qwen2.5:1.5b`.
- Modèle local d'embeddings : `nomic-embed-text`.
- Test local réussi : recommandations pour Citron/Lime en boissons.
- **Statut à annoncer honnêtement :** aucun modèle fine-tuné ni déploiement
  distant n'est fourni par ces livrables ; la publication reste à faire si
  elle est attendue.

### Slide — Évaluation sur 30 cas

**Titre :** Vérification fonctionnelle sur un jeu de référence synthétique

**À afficher :**

- 30 cas, 5 par famille.
- Détection : famille 100 %, application 100 %.
- Cible top 4 : 56,7 % avant filtres, 100 % après filtres SQL.
- Respect des contraintes SQL : 100 %.
- Retrieval + filtres : moyenne 390 ms, p95 543 ms.

**Note en bas de slide :** Test d'intégration non aveugle sur données
synthétiques ; Qwen n'a pas été évalué sur les 30 cas. Une seule requête
end-to-end locale a été observée à 2,30 s ; ce chiffre n'est pas une mesure
statistique.

### Slide — Prochaines étapes avant industrialisation

**À afficher :**

1. Remplacer les attributs fictifs par des données autorisées, vérifiées et
   gouvernées.
2. Obtenir une validation métier des familles, synonymes, applications et
   règles de filtrage.
3. Déployer l'API et les modèles dans un environnement cible, puis tester le
   contrat réseau depuis la plateforme.
4. Constituer un jeu de test indépendant et faire une évaluation end-to-end
   complète, notamment de la qualité Qwen et de la latence p95.
