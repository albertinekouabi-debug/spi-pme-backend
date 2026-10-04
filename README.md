# SPI-PME — Backend Django (Phase 4 complète : 10 modules)

Pile : Django 5.x/6.x + Django REST Framework + Simple JWT + PostgreSQL
(conforme au cahier des charges v2.1, §7-§8).

Modules : `core`, `accounts`, `registry`, `resources`, `treasury`, `tasks`,
`intelligence`, `alerts`, `imports`, `audit`.

## Démarrage : configuration obligatoire

Le serveur **refuse de démarrer** sans `DJANGO_SECRET_KEY` (>= 32 caractères,
hors `DJANGO_DEBUG=True`, où une clé éphémère est générée). Voir `spi_pme/settings.py`.
Hors développement : cookies sécurisés, HSTS et redirection HTTPS actifs par défaut.

```
export DJANGO_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(50))')"
export DB_PASSWORD=...          # + DB_NAME, DB_USER, DB_HOST, DB_PORT
python -m pytest                # utilise spi_pme.settings_test (clé de test dédiée)
```

Import de fichiers : 5 Mo max, 10 000 lignes max, formats `.csv` et `.xlsx` uniquement.

## État réel, vérifié

- `python manage.py check` : OK
- Migrations générées sans erreur (10 apps)
- **188 tests automatisés, tous passants** (PostgreSQL 16, Django 5.1), aucune régression à chaque étape
  (règle appliquée systématiquement depuis le milieu du projet)
- `seed_rbac` et `seed_parametres_conformite` idempotents

## Bug transversal trouvé et corrigé — typage JSON des champs Decimal

En préparant les DTO Android du module `treasury`, un test volontairement
strict (vérification du JSON brut, pas de `response.data` déjà re-parsé)
a révélé que DRF sérialise un `Decimal` renvoyé dans un dict brut
(`Response({...})`, hors `Serializer`) en **nombre flottant** (`0.0`), pas
en chaîne — contrairement à ce qui se passe pour un champ `DecimalField`
d'un vrai `Serializer`. Un client au typage strict (Android/kotlinx.serialization)
attendant une chaîne aurait échoué de façon incohérente selon qu'il y a des
données ou non.

**Corrigé** en forçant `str(...)` explicitement sur toutes les valeurs
`Decimal` des réponses en dict brut :
- `treasury` : `/transactions/summary/`, `/transactions/evolution/`
- `resources` : `/resources/{id}/evolution/`, `/resources/{id}/seuils-recommandes/`

Tests mis à jour en conséquence (comparaison via `Decimal(...)` plutôt
qu'une égalité directe avec un nombre), plus un test dédié qui vérifie le
JSON brut pour empêcher une régression silencieuse (`response.data`, déjà
re-parsé côté Python, aurait masqué le problème).

Recherche web effectuée avant implémentation (sources concordantes, juillet
2026) : le Règlement CEMAC du 11 avril 2016 relatif à la LBC/FT/P, révisé
par le Règlement n°02/24/CEMAC/UMAC/CM du 20 décembre 2024, interdit les
paiements en espèces et impose leur déclaration au-delà de **5 000 000 FCFA**
— ainsi que toute transaction jugée suspecte, quel qu'en soit le montant.

**Implémentation** (`apps.treasury.compliance`) :
- Le seuil vit dans `ParametreSysteme` (table de configuration, pas une
  constante Python) — `seed_parametres_conformite` l'initialise avec sa
  source citée, mais un administrateur peut le modifier sans déploiement de
  code si la réglementation change. **Testé explicitement** : changer la
  valeur en base change le comportement de détection, sans toucher au code.
- Détection automatique à la création d'une `Transaction` en espèces
  (`mode_paiement="especes"`) dépassant le seuil configuré → création d'une
  `DeclarationConformite`.
- Signalement manuel possible quel que soit le montant (`/signal`), pour les
  transactions "jugées suspectes" au sens du règlement.
- Actions explicites `/declare` (référence ANIF obligatoire) et `/exempt`
  (motif obligatoire), verrouillées contre la double décision
  (`select_for_update`), même garantie que `intelligence`/`alerts`.
- **Aucun seuil non vérifiable n'a été codé en dur** : la TVA reste un champ
  saisi par l'utilisateur (`Facture.taux_tva`), pas un taux imposé — le taux
  légal congolais (18%, + 5% de centimes additionnels) trouvé en recherche
  n'a pas été figé dans le modèle car il peut varier par régime fiscal et
  change par décret ; l'utilisateur reste responsable de saisir le taux
  applicable à son cas.

## Module `audit` — Journal d'audit (§5.10, §13.4, FR-AUD-*)

**Lecture seule à double garantie**, pas seulement documentaire :
- ORM : `JournalAudit.save()` refuse toute écriture sur une ligne déjà
  persistée, `delete()` refuse systématiquement — testé.
- Base de données (PostgreSQL uniquement, migration `0002_triggers_...`,
  sans effet sur sqlite) : triggers `BEFORE UPDATE/DELETE` levant une
  exception, pour qu'un accès direct à la base (hors ORM Django) ne puisse
  pas non plus altérer le journal.

**Réellement alimenté**, pas un journal vide avec une API de lecture
inutile : intégré aux actions sensibles déjà construites — connexions
réussies/échouées (`accounts`), création/désactivation de comptes
(`accounts`), validation/rejet de suggestions IA (`intelligence`),
résolution/ignorance d'alertes (`alerts`), déclarations de conformité
(`treasury`), imports de fichiers (`imports`). Chaque intégration est
couverte par un test qui vérifie qu'une entrée est bien créée en base, pas
seulement que l'appel ne plante pas.

Accès réservé aux rôles Administrateur et Auditeur (`audit.read`, seed_rbac)
— pas de périmètre sectoriel, la traçabilité doit rester globale à
l'instance.

À la demande du porteur de projet, ajout de fonctionnalités manquantes des
maquettes mais nécessaires en pratique — **aucun chiffre métier codé en dur**,
conformément à la contrainte explicite du projet :

- **`registry.Entite`** : `numero_rccm` et `numero_fiscal` (NIU), identifiants
  standards en zone OHADA/CEMAC (Congo-Brazzaville inclus), optionnels.
- **`treasury.Facture`** : `taux_tva` optionnel, saisi par l'utilisateur
  (jamais un taux imposé par le code), avec `montant_tva`/`montant_ttc`
  calculés automatiquement.
- **`resources` — seuils recommandés, PAS des seuils "standards"** :
  `GET /api/v1/resources/{id}/seuils-recommandes/?delai_jours=X` calcule une
  suggestion à partir de la **consommation réelle enregistrée** de la
  ressource (formule de stock de sécurité), `delai_jours` étant obligatoire
  et fourni par l'utilisateur — le système ne peut pas deviner le délai de
  réapprovisionnement de ses fournisseurs. Aucune valeur "normale par
  secteur" n'a été codée : un hôpital et une épicerie n'ont rien de
  comparable en la matière, une telle table aurait été arbitraire.
- **`imports`** : colonnes optionnelles "Seuil critique"/"Seuil d'alerte"
  dans les fichiers importés, appliquées à la ressource si fournies.

**Bugs trouvés et corrigés pendant cette passe** (voir critical_reminders
sur la vérification systématique) :
1. Le gestionnaire d'erreurs centralisé (`apps/core/exceptions.py`) ne
   traitait que les erreurs de champ encapsulées en liste par un serializer
   ; un `ValidationError({"champ": "texte"})` levé à la main (comme dans la
   nouvelle vue seuils-recommandes) perdait son détail. Corrigé pour gérer
   les deux formes.
2. Une assertion de test comparait un `Decimal` à une chaîne de caractères
   (`Decimal("1.5") == "1.5"` est faux en Python) — bug de test, pas de code,
   corrigé.

**Point volontairement NON traité, signalé plutôt que deviné** : les seuils
réglementaires (déclaration de transactions suspectes/AML, taux de TVA
officiel en vigueur, seuils fiscaux) ne sont pas implémentés, car je ne
veux pas citer de mémoire un chiffre légal qui pourrait être obsolète ou
faux dans un système destiné à la production. Si une fonctionnalité de
conformité réglementaire est nécessaire, une recherche des sources
officielles actuelles (COBAC/CEMAC, DGI Congo) devrait être faite au
moment de la spécifier, pas supposée par le modèle.

## Module `imports` — Import de données (§5.8, FR-IMP-*)

- **Parsing réel** CSV et XLSX (`openpyxl`), pas de simulation — testé avec
  de vrais fichiers générés en mémoire, y compris la normalisation des
  en-têtes accentuées ("Catégorie", "Entrepôt", "Valeur unitaire").
- **Deux endpoints séparés**, pas un seul POST ambigu :
  `/preview` (parse + valide, **aucune écriture**, correspond aux étapes
  "Valider"/"Aperçu" de la maquette) et `/commit` (parse + valide + persiste,
  étapes "Importer"/"Terminé").
- **Import partiel assumé** : les lignes valides sont importées même si
  d'autres lignes du même fichier sont en erreur (statut
  `termine_avec_anomalies`), conformément à la maquette (528 lignes,
  12 erreurs, import quand même utile). `echec` seulement si aucune ligne
  n'est valide.
- **Upsert par (secteur, nom)** insensible à la casse : une ressource déjà
  connue voit son niveau/sa valeur mis à jour sans écraser ses seuils déjà
  configurés ; une ressource inconnue est créée sans seuils.

**Changement de modèle nécessaire, documenté** : `Ressource.seuil_critique`
et `seuil_alerte` sont désormais **nullables**. Un fichier importé ne fournit
aucun seuil (voir les colonnes de la maquette) ; plutôt que d'inventer des
valeurs par défaut arbitraires, `calculer_statut()` retourne `"stable"` tant
qu'ils ne sont pas configurés manuellement. Migration de relâchement de
contrainte uniquement — rétrocompatible, aucun test existant cassé.

**Limite de portée signalée plutôt que masquée** : la maquette montre
"Catégorie invalide : la catégorie 'Boissons' n'existe pas", ce qui suppose
une liste de catégories autorisées par secteur. Cette liste n'existe dans
aucune table du modèle de données actuel — je valide donc la présence de la
catégorie, pas son appartenance à une liste. Ajouter cette liste blanche est
possible mais suppose d'abord de décider où elle est configurée
(`ConfigurationSectorielle` ? une nouvelle table ?) — question ouverte pour
le porteur de projet plutôt qu'une hypothèse silencieuse de ma part.

## Module `alerts` — Alertes (§5.7, FR-ALR-*)

Même philosophie que `intelligence` : détecteurs pluggables
(`apps/alerts/detectors.py`, `REGISTRE`), mais avec une **réconciliation**
plutôt qu'une simple génération — à chaque exécution, l'état observé
maintenant est comparé à ce qui est déjà actif en base :
- condition nouvelle → création
- condition toujours vraie → aucun doublon
- condition disparue (ex. stock réapprovisionné) → **résolution automatique**,
  testée explicitement, pour ne jamais laisser une alerte "active" orpheline.

5 détecteurs : `stock_critique`, `stock_a_surveiller` (déjà calculés par
`resources`), `facture_impayee` (niveau selon le nombre de jours de retard),
`tache_en_retard`, `consommation_elevee` (comparaison semaine courante vs
semaine précédente sur les mouvements de stock — silencieux si l'historique
sur l'une des deux semaines manque, pour éviter un faux signal de démarrage).

Comme `intelligence` : `ReadOnlyModelViewSet` (aucune route PATCH générique),
transitions de statut uniquement via `/resolve` et `/ignore`, verrouillage
`select_for_update` contre la double décision concurrente.

Note d'architecture : le CDC (§10.2) ne relie l'Alerte qu'à Ressource/Entite/
Tache. J'ai ajouté un lien optionnel vers `Facture` (module construit après
la rédaction du schéma), nécessaire pour l'alerte "Facture impayée" visible
dans la maquette — documenté dans `apps/alerts/models.py`, pas ajouté en
silence.

## Module `intelligence` — Suggestions IA (§5.6, §11.3, FR-SUG-*)

Architecture à moteurs interchangeables (`apps/intelligence/algorithms/`) :
chaque algorithme implémente un contrat commun (`Algorithme.generer(secteur)
-> list[SuggestionDraft]`) et s'enregistre dans `REGISTRE` — en ajouter un
nouveau ne touche ni aux vues, ni aux services, ni aux sérialiseurs.

- **`seuil`** : réapprovisionnement basé sur les seuils sectoriels déjà
  calculés par `resources` (critique/à surveiller). Impact financier réel
  (`quantité × valeur_unitaire`) si la donnée existe, sinon `None` —
  jamais un montant inventé.
- **`regression_lineaire`** : détecte une tendance de consommation baissière
  sur les mouvements de stock des 14 derniers jours et projette une date de
  rupture. **Une seule requête agrégée pour tout le secteur** (GROUP BY
  ressource/jour), régression vectorisée avec `numpy.polyfit` — pas de N+1,
  pas de boucle Python sur les points de données.

**Garantie structurelle FR-SUG-03** (aucune exécution automatique) :
`SuggestionViewSet` est un `ReadOnlyModelViewSet` — il n'existe *aucune*
route de modification générique. La seule voie vers un effet réel
(création d'une `Transaction` de mouvement de stock) est
`apps.intelligence.services.valider_suggestion`, appelée uniquement par
l'action `POST /suggestions/{id}/validate/`, sous verrou transactionnel
(`select_for_update`) pour empêcher toute double exécution en cas de double
clic ou de requêtes concurrentes.

Un champ a été ajouté à `resources.Ressource` en cours de route :
`valeur_unitaire` (nullable), visible dans la maquette d'import de données
mais absent du modèle initial — nécessaire pour calculer un impact financier
réel plutôt qu'un chiffre fabriqué.

`python manage.py generate_suggestions [--secteur commerce]` : commande
pensée pour être planifiée (cron), pas déclenchée à chaque requête HTTP —
l'algorithme de tendance agrège un historique et ne doit pas alourdir le
temps de réponse d'un écran consulté par un utilisateur.

## Démarrage local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Variables d'environnement minimales (voir spi_pme/settings.py) :
export DB_NAME=spi_pme DB_USER=spi_pme DB_PASSWORD=... DB_HOST=localhost

python manage.py migrate
python manage.py seed_rbac
python manage.py createsuperuser   # accès /admin uniquement, pas le RBAC applicatif
python manage.py runserver
```

Pour lancer les tests sans PostgreSQL (sqlite en mémoire) :
créer temporairement un `spi_pme/settings_test.py` qui surcharge `DATABASES`
avec le backend `sqlite3`, puis `DJANGO_SETTINGS_MODULE=spi_pme.settings_test
pytest`. Ce fichier ne doit pas être livré en production (absent de ce zip).

## Endpoints livrés

| Méthode | URL | Description | Permission requise |
|---|---|---|---|
| POST | `/api/v1/auth/login` | Connexion (email OU nom d'utilisateur) | — |
| POST | `/api/v1/auth/refresh` | Renouvellement du token d'accès | — |
| POST | `/api/v1/auth/logout` | Révocation du refresh token | authentifié |
| GET/POST | `/api/v1/users/` | Gestion des comptes | Administrateur |
| GET/POST | `/api/v1/roles/` | Gestion des rôles | Administrateur |
| GET | `/api/v1/permissions/` | Catalogue des permissions | Administrateur |
| GET/POST/PATCH/DELETE | `/api/v1/entities/` | Registre (clients, fournisseurs...) | `registry.read` / `registry.write` |
| GET | `/api/v1/entities/summary/` | Total + répartition par type réellement présent dans le périmètre | `registry.read` |
| GET/POST/PATCH/DELETE | `/api/v1/resources/` | Stock / Ressources | `resources.read` / `resources.write` |
| GET | `/api/v1/resources/summary/` | Répartition par statut (cartes du tableau de bord) | `resources.read` |
| GET | `/api/v1/resources/{id}/evolution/?jours=7` | Historique du niveau de stock (reconstitué depuis les transactions) | `resources.read` |
| GET | `/api/v1/resources/{id}/seuils-recommandes/?delai_jours=X&facteur_securite=Y` | Suggestion de seuils basée sur la consommation réelle | `resources.read` |
| GET/POST/PATCH/DELETE | `/api/v1/transactions/` | Entrées, sorties, mouvements de stock | `treasury.read` / `treasury.write` |
| GET | `/api/v1/transactions/summary/` | Cartes Trésorerie (entrées/sorties du mois, solde net, solde disponible) | `treasury.read` |
| GET | `/api/v1/transactions/evolution/?jours=7` | Solde cumulé jour par jour (graphe Trésorerie) | `treasury.read` |
| GET/POST/PATCH/DELETE | `/api/v1/invoices/` | Factures et relances | `treasury.read` / `treasury.write` |
| GET/POST/PATCH/DELETE | `/api/v1/tasks/` | Tâches | `tasks.read` / `tasks.write` |
| GET | `/api/v1/tasks/summary/` | Cartes Toutes/Terminées/En cours/En retard | `tasks.read` |
| GET | `/api/v1/tasks/{id}/history/` | Historique des changements de statut | `tasks.read` |
| GET | `/api/v1/suggestions/` | Liste des suggestions IA | `intelligence.read` |
| POST | `/api/v1/suggestions/generate/` | Déclenche le moteur d'algorithmes pour un secteur | `intelligence.write` |
| POST | `/api/v1/suggestions/{id}/validate/` | Valide et exécute (crée une Transaction) | `intelligence.write` |
| POST | `/api/v1/suggestions/{id}/reject/` | Rejette (motif obligatoire) | `intelligence.write` |
| GET | `/api/v1/suggestions/summary/` | Cartes Total/Validées/En attente/Rejetées + taux d'acceptation | `intelligence.read` |
| GET | `/api/v1/alerts/` | Liste des alertes | `alerts.read` |
| POST | `/api/v1/alerts/generate/` | Exécute les détecteurs (création + résolution auto) | `alerts.write` |
| POST | `/api/v1/alerts/{id}/resolve/` | Marque comme traitée | `alerts.write` |
| POST | `/api/v1/alerts/{id}/ignore/` | Marque comme ignorée | `alerts.write` |
| GET | `/api/v1/alerts/summary/` | Cartes Critique/Élevée/Modérée/Résolue | `alerts.read` |
| GET | `/api/v1/imports/` | Historique des imports | `imports.read` |
| POST | `/api/v1/imports/preview/` | Aperçu + rapport d'erreurs, sans écriture (multipart : `fichier`, `secteur`) | `imports.write` |
| POST | `/api/v1/imports/commit/` | Import réel, partiel si anomalies (multipart : `fichier`, `secteur`) | `imports.write` |

Filtres disponibles sur `/api/v1/entities/` : `?q=` (nom/type/id),
`?type=`, `?statut=`, `?secteur=`.
Filtres disponibles sur `/api/v1/resources/` : `?q=`, `?type=`, `?statut=`,
`?secteur=`. Le champ `statut` est **calculé automatiquement** côté serveur
(FR-STK-02) — toute valeur envoyée par le client est ignorée et recalculée.
Filtres disponibles sur `/api/v1/transactions/` : `?type=`, `?secteur=`,
`?entite=`, `?ressource=`, `?date_debut=`, `?date_fin=`.
Filtres disponibles sur `/api/v1/tasks/` : `?statut=`, `?priorite=`,
`?assignee=`, `?categorie=`, `?secteur=`, `?retard=true`, `?q=`.
Le périmètre est automatiquement restreint aux secteurs de l'utilisateur sur
tous ces endpoints, sauf pour le rôle Administrateur (accès total).

## Comportement notable : Transaction ↔ Ressource

Une `Transaction` de type `mouvement_stock` répercute sa `quantite` (signée :
positive = réapprovisionnement, négative = consommation/vente) sur
`Ressource.niveau_actuel`, ce qui recalcule automatiquement son `statut`
(critique/à surveiller/stable). C'est ce mécanisme, déjà couvert par des
tests, qui alimente aussi `/api/v1/resources/{id}/evolution/`.

## Décisions à valider par le porteur de projet

1. **`utilisateur.secteur_principal` + M2M `secteurs`** — résout une
   contradiction du CDC (§10.2 vs §10.3). Détail dans
   `spi_pme_modele_donnees_analyse.md` livré précédemment.
2. **L'Administrateur a un accès total garanti par le code**, pas seulement
   par les permissions attribuées via `seed_rbac` — évite un verrouillage
   total de l'instance si le seed n'a pas encore été exécuté.

## Module restant

`audit` (journal en lecture seule côté API, déjà verrouillé en base et
alimenté au fil des autres modules — dernier module avant la Phase 5,
application Android).
