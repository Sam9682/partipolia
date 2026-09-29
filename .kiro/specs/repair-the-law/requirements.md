# Requirements Document

_Document d'Exigences — « Réparer la loi » (MVP), fonctionnalité de la plateforme PARTIPOLIA_

## Introduction

« Réparer la loi » est la première fonctionnalité du MVP de PARTIPOLIA, une plateforme de fabrication
citoyenne de la loi (« GitHub de la loi »). La fonctionnalité présente un catalogue de **Problèmes
Juridiques** concrets signalés par les citoyens (par exemple l'occupation illégale d'un logement) et,
pour chacun, organise un parcours de compréhension puis de délibération :

1. exposer le problème et le droit en vigueur, en distinguant le problème réel de la simplification
   médiatique ;
2. présenter plusieurs **Réformes Proposées** (options législatives) que le citoyen compare, sans que
   l'IA ne décide à sa place ;
3. simuler les **Conséquences** par catégorie d'acteurs concernés à 1, 5 et 10 ans (« LoiLab ») ;
4. détecter les **Effets Pervers** et contradictions via des **Agents IA Contradictoires** spécialisés
   produisant une **Conclusion Technique** agrégée ;
5. permettre un **Vote Citoyen** proposition par proposition, avec amendements et signalements ;
6. conserver un **Historique de Versions** de type Git des propositions de loi.

Cette fonctionnalité **réutilise les fondations existantes** de la spec `partipolia-platform` :
le modèle `Proposal` et `ProposalVersion` (versionnement immuable, `snapshot` jsonb + `change_summary`),
le `Vote` à trois valeurs, les `Argument` (FOR/AGAINST), les `Comment`, les `Source`/`Document`, et le
`Pipeline_RAG` complet (classification → réécriture → recherche hybride → fusion → reranking →
construction de contexte → génération → validation des citations). Elle **n'introduit de nouveaux
modèles que lorsque les modèles existants ne suffisent pas**, et s'appuie sur les services existants
(`proposal_service`, `vote_service`, `argument_service`, `source_service`, `theme_service`,
`statistics_service`, `moderation_service`).

**Principe directeur :** l'IA **ne décide pas** et **ne vote pas**. Elle **organise, documente et
augmente** la capacité des citoyens à comprendre le droit et à construire les textes. Toute analyse
juridique produite par l'IA **cite ses Sources** (Légifrance, jurisprudence, textes réglementaires) et
distingue explicitement le **problème réel** de la **simplification médiatique**.

### Périmètre du MVP (inclus)

- Une rubrique « Réparer la loi » listant environ **10 Problèmes Juridiques** concrets, avec pour chacun :
  nombre de citoyens concernés, textes de loi concernés, jurisprudence, niveau de complexité.
- Pour chaque Problème Juridique : **analyse du droit actuel** par le Pipeline_RAG juridique
  (ce que dit la loi, où est le blocage, procédures existantes, effets pervers), avec citations.
- **3 à 5 Réformes Proposées** par Problème Juridique, comparables entre elles, incluant l'option de
  statu quo.
- **Simulation des Conséquences** par catégorie d'acteurs à 1, 5 et 10 ans (« LoiLab »).
- **Détection d'Effets Pervers** et de contradictions (mode « avocat du diable » : risques et
  contre-mesures).
- **Agents IA Contradictoires** spécialisés (JuristeIA, BudgetIA, ConstitutionIA, ImpactIA, OpposantIA,
  DéfenseurIA, SimulationIA) et **Conclusion Technique** agrégée.
- **Vote Citoyen** proposition par proposition (approuver, rejeter, proposer un amendement, demander une
  explication, signaler un effet secondaire), en réutilisant `Vote`.
- **Historique de Versions** de type Git des Réformes Proposées, en réutilisant `ProposalVersion`.
- Transparence des Sources juridiques (RAG avec citations Légifrance/jurisprudence) et neutralité de l'IA.

### Hors périmètre du MVP (évolutions futures)

- Le tableau de bord budgétaire global « Où va l'argent ? ».
- Le « programme citoyen complet » agrégeant l'ensemble des réformes en un programme.
- La construction automatique d'un Programme définitif à partir des votes citoyens.

> Ces évolutions sont mentionnées comme trajectoire future et **ne font pas** partie du présent MVP.

### Décisions de périmètre validées

- **Réutilisation prioritaire** : une Réforme Proposée EST une `Proposal` existante ; son historique
  utilise `ProposalVersion` ; le vote utilise `Vote` ; les arguments utilisent `Argument` ; l'analyse
  juridique utilise le `Pipeline_RAG`. Aucun modèle redondant n'est créé.
- **Rôle non-décisionnel de l'IA** : l'IA produit des analyses, simulations et arguments documentés ;
  elle ne recommande jamais un vote individuel et ne conclut jamais à la place du citoyen.
- **Transparence juridique** : toute affirmation juridique importante est associée à une Source
  (Légifrance, jurisprudence, texte réglementaire) via le mécanisme de citations du Pipeline_RAG.
- **Distinction problème réel / simplification médiatique** : chaque analyse de Problème Juridique
  sépare explicitement ces deux registres.
- **Langue** : interface et API en français.

## Glossary

- **Plateforme** : l'ensemble applicatif PARTIPOLIA, tel que défini par la spec `partipolia-platform`.
- **API** : l'interface REST versionnée sous le préfixe `/api/v1`.
- **Interface_Publique** : les pages accessibles sans authentification.
- **Utilisateur_Authentifié** : Utilisateur dont la session JWT est active (défini par
  `partipolia-platform`).
- **Rubrique_Réparer_La_Loi** : la section de la Plateforme présentant le catalogue des Problèmes
  Juridiques et leurs parcours de compréhension et de délibération.
- **Problème_Juridique** : entité décrivant un problème de droit signalé par des citoyens (attributs :
  slug, titre, résumé, categorie/theme_id, nombre de citoyens concernés, textes de loi concernés,
  références de jurisprudence, niveau de complexité, statut).
- **Niveau_De_Complexité** : valeur qualifiant la difficulté d'un Problème_Juridique parmi FAIBLE,
  MOYEN et ELEVE.
- **Analyse_Juridique** : synthèse produite par le Pipeline_RAG pour un Problème_Juridique, décrivant
  ce que dit la loi, où se situe le blocage, les procédures existantes et les effets pervers, avec
  citations de Sources.
- **Réforme_Proposée** : une option législative rattachée à un Problème_Juridique. Une Réforme_Proposée
  est représentée par une `Proposal` existante ; l'ensemble des Réformes_Proposées d'un même
  Problème_Juridique inclut une option de statu quo.
- **Option_Statu_Quo** : la Réforme_Proposée représentant l'absence de modification du droit en vigueur.
- **Simulation_De_Conséquences** : analyse projetant, pour une Réforme_Proposée, les effets par
  Catégorie_D_Acteur aux horizons 1 an, 5 ans et 10 ans (fonctionnalité « LoiLab »).
- **Catégorie_D_Acteur** : population ou institution concernée par une Réforme_Proposée (par exemple
  propriétaire, locataire, occupant, tribunal, police, préfecture, budget de l'État).
- **Détection_D_Effets_Pervers** : analyse identifiant les risques, effets indésirables et
  contradictions d'une Réforme_Proposée, avec des contre-mesures possibles (mode « avocat du diable »).
- **Agent_IA_Contradictoire** : agent d'analyse spécialisé fondé sur le Pipeline_RAG, appartenant à
  l'ensemble {JuristeIA, BudgetIA, ConstitutionIA, ImpactIA, OpposantIA, DéfenseurIA, SimulationIA}.
- **JuristeIA** : Agent_IA_Contradictoire évaluant la cohérence juridique.
- **BudgetIA** : Agent_IA_Contradictoire évaluant les conséquences financières.
- **ConstitutionIA** : Agent_IA_Contradictoire évaluant les risques constitutionnels.
- **ImpactIA** : Agent_IA_Contradictoire évaluant l'effet sur les populations concernées.
- **OpposantIA** : Agent_IA_Contradictoire identifiant les failles d'une Réforme_Proposée.
- **DéfenseurIA** : Agent_IA_Contradictoire formulant les meilleurs arguments en faveur d'une
  Réforme_Proposée.
- **SimulationIA** : Agent_IA_Contradictoire produisant les scénarios de la Simulation_De_Conséquences.
- **Conclusion_Technique** : synthèse agrégée et non prescriptive des analyses des
  Agents_IA_Contradictoires pour une Réforme_Proposée.
- **Vote_Citoyen** : expression d'une préférence d'un Utilisateur_Authentifié sur une Réforme_Proposée,
  réutilisant le modèle `Vote` (valeurs +1, 0, -1).
- **Amendement** : nouvelle Version_De_Proposition d'une Réforme_Proposée proposée par un
  Utilisateur_Authentifié (réutilise `ProposalVersion`).
- **Signalement_D_Effet_Secondaire** : contribution par laquelle un Utilisateur_Authentifié signale un
  effet secondaire potentiel d'une Réforme_Proposée.
- **Version_De_Proposition** : entrée d'historique complète et immuable d'une `Proposal`
  (`proposal_versions`, `snapshot` jsonb + `change_summary`), telle que définie par
  `partipolia-platform`.
- **Source** : référence documentaire (title, url, publisher, source_type, publication_date,
  is_verified) ; pour cette fonctionnalité, source_type inclut notamment LEGISLATION.
- **Source_Juridique** : Source dont source_type vaut LEGISLATION ou qui référence Légifrance, un texte
  réglementaire ou une décision de jurisprudence.
- **Pipeline_RAG** : la chaîne classification → réécriture → recherche hybride → fusion → reranking →
  construction de contexte → génération → validation des citations, définie par `partipolia-platform`.
- **Assistant_IA** : le service conversationnel documentaire fondé sur le Pipeline_RAG.
- **Simplification_Médiatique** : présentation simplifiée ou partielle d'un Problème_Juridique dans le
  débat public, à distinguer du problème réel.
- **JWT** : jeton d'authentification (access token + refresh token).

## Requirements

### Exigence 1 — Rubrique « Réparer la loi » et catalogue de Problèmes Juridiques

**User Story:** En tant que citoyen, je veux parcourir une rubrique listant des problèmes juridiques
concrets, afin de choisir un problème à comprendre et sur lequel délibérer.

#### Critères d'acceptation

1. THE Plateforme SHALL fournir une Rubrique_Réparer_La_Loi présentant la liste des Problèmes_Juridiques.
2. THE Plateforme SHALL initialiser la Rubrique_Réparer_La_Loi avec au moins 10 Problèmes_Juridiques
   concrets pour le MVP.
3. WHEN un visiteur consulte la Rubrique_Réparer_La_Loi, THE Interface_Publique SHALL afficher, pour
   chaque Problème_Juridique, son titre, son résumé, le nombre de citoyens concernés, les textes de loi
   concernés, les références de jurisprudence et son Niveau_De_Complexité.
4. WHEN un client de l'API sollicite `GET /api/v1/legal-problems`, THE Plateforme SHALL retourner la
   liste paginée des Problèmes_Juridiques avec une taille de page par défaut de 20, une taille de page
   maximale de 100 et le nombre total de Problèmes_Juridiques disponibles.
5. WHEN un client de l'API sollicite `GET /api/v1/legal-problems/{id}` avec un identifiant existant,
   THE Plateforme SHALL retourner le détail du Problème_Juridique correspondant.
6. IF l'identifiant transmis à `GET /api/v1/legal-problems/{id}` ne correspond à aucun
   Problème_Juridique existant, THEN THE Plateforme SHALL rejeter la requête, ne retourner aucun détail
   et indiquer au demandeur une erreur signalant la ressource introuvable.
7. THE Interface_Publique SHALL afficher la Rubrique_Réparer_La_Loi sans exiger d'authentification pour
   la consultation.

### Exigence 2 — Objet Problème Juridique

**User Story:** En tant que citoyen, je veux qu'un problème juridique soit décrit de manière structurée,
afin de le comparer et de le comprendre objectivement.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter chaque Problème_Juridique avec les attributs : slug, titre, résumé,
   theme_id, nombre de citoyens concernés, textes de loi concernés, références de jurisprudence,
   Niveau_De_Complexité et statut.
2. THE Plateforme SHALL restreindre le Niveau_De_Complexité aux valeurs de l'ensemble
   {FAIBLE, MOYEN, ELEVE}.
3. IF un Niveau_De_Complexité soumis pour un Problème_Juridique n'appartient pas à l'ensemble
   {FAIBLE, MOYEN, ELEVE}, THEN THE Plateforme SHALL refuser l'enregistrement et retourner une
   indication d'erreur précisant que le Niveau_De_Complexité est invalide.
4. THE Plateforme SHALL rattacher chaque Problème_Juridique à exactement un Thème du
   Référentiel_Thématique existant.
5. THE Plateforme SHALL rattacher à chaque Problème_Juridique au moins une Réforme_Proposée
   représentant l'Option_Statu_Quo.
6. WHEN un Problème_Juridique est créé, THE Plateforme SHALL lui attribuer un identifiant unique et un
   slug unique.

### Exigence 3 — Analyse du droit actuel par le Pipeline RAG juridique

**User Story:** En tant que citoyen, je veux comprendre ce que dit réellement le droit sur un problème,
afin de distinguer le problème réel de sa simplification médiatique.

#### Critères d'acceptation

1. WHEN un Utilisateur consulte un Problème_Juridique, THE Assistant_IA SHALL produire une
   Analyse_Juridique via le Pipeline_RAG décrivant ce que dit la loi, où se situe le blocage, les
   procédures existantes et les effets pervers du droit actuel.
2. THE Analyse_Juridique SHALL distinguer explicitement le problème réel de la Simplification_Médiatique.
3. THE Analyse_Juridique SHALL associer chaque Affirmation_Juridique à au moins une Source_Juridique au
   moyen des citations du Pipeline_RAG, une Affirmation_Juridique étant un énoncé de l'Analyse_Juridique
   portant sur le contenu, la portée ou l'interprétation d'un texte de loi, d'un règlement ou d'une
   décision de jurisprudence.
4. IF l'information juridique nécessaire est absente du contexte documentaire du Pipeline_RAG, THEN THE
   Assistant_IA SHALL indiquer explicitement que l'information est indisponible et SHALL s'abstenir de
   produire une Affirmation_Juridique non fondée sur ce contexte.
5. IF l'étape de récupération documentaire du Pipeline_RAG échoue ou ne retourne aucun document, THEN
   THE Assistant_IA SHALL s'abstenir de produire l'Analyse_Juridique et SHALL afficher un message
   indiquant que l'analyse est momentanément indisponible.
6. THE Assistant_IA SHALL s'abstenir d'inventer des références de loi, de jurisprudence ou de textes
   réglementaires.
7. THE Analyse_Juridique SHALL afficher ses citations sous forme de Sources numérotées renvoyant à
   Légifrance, à un texte réglementaire ou à une décision de jurisprudence.

### Exigence 4 — Réformes proposées et comparaison

**User Story:** En tant que citoyen, je veux comparer plusieurs options de réforme pour un problème,
afin de me forger un avis sans que l'IA ne choisisse à ma place.

#### Critères d'acceptation

1. THE Plateforme SHALL rattacher à chaque Problème_Juridique entre 3 et 5 Réformes_Proposées
   inclusivement, dont exactement une Option_Statu_Quo.
2. IF le nombre de Réformes_Proposées rattachées à un Problème_Juridique est inférieur à 3 ou supérieur
   à 5, ou si le nombre d'Option_Statu_Quo rattachée diffère de 1, THEN THE Plateforme SHALL rejeter la
   configuration du Problème_Juridique et retourner une indication d'erreur signalant la cardinalité
   invalide des Réformes_Proposées.
3. THE Plateforme SHALL représenter chaque Réforme_Proposée par une `Proposal` existante rattachée au
   Problème_Juridique.
4. WHEN un Utilisateur consulte un Problème_Juridique, THE Interface_Publique SHALL afficher
   simultanément l'ensemble des Réformes_Proposées du Problème_Juridique, chacune identifiée
   distinctement, l'Option_Statu_Quo étant signalée comme telle.
5. WHEN un Utilisateur demande de comparer les Réformes_Proposées d'un Problème_Juridique, THE
   Assistant_IA SHALL les comparer sur chacun des critères suivants : objectif, procédure, coût estimé,
   calendrier, effets documentés, contraintes juridiques, Sources et incertitudes.
6. IF, pour une Réforme_Proposée comparée, l'information documentée relative à un critère est absente du
   contexte documentaire du Pipeline_RAG, THEN THE Assistant_IA SHALL indiquer que l'information est
   indisponible pour ce critère et SHALL s'abstenir d'inférer une valeur pour ce critère.
7. THE Assistant_IA SHALL s'abstenir de désigner une Réforme_Proposée comme préférable, de formuler une
   recommandation de vote et de classer les Réformes_Proposées par ordre de préférence.

### Exigence 5 — Simulation des conséquences (« LoiLab »)

**User Story:** En tant que citoyen, je veux visualiser les conséquences d'une réforme par catégorie
d'acteurs et dans le temps, afin d'anticiper ses effets concrets.

#### Critères d'acceptation

1. WHEN un Utilisateur demande la Simulation_De_Conséquences d'une Réforme_Proposée, THE SimulationIA
   SHALL produire des effets projetés par Catégorie_D_Acteur.
2. THE Simulation_De_Conséquences SHALL fournir, pour chaque Catégorie_D_Acteur, un effet projeté à
   l'horizon 1 an, un effet projeté à l'horizon 5 ans et un effet projeté à l'horizon 10 ans.
3. THE Simulation_De_Conséquences SHALL énumérer les Catégories_D_Acteur pertinentes pour le
   Problème_Juridique concerné.
4. THE SimulationIA SHALL fonder chaque effet projeté sur le contexte documentaire du Pipeline_RAG et
   SHALL associer chaque effet documenté à ses Sources.
5. WHERE un effet projeté repose sur une hypothèse, THE SimulationIA SHALL accompagner cet effet d'un
   marqueur indiquant qu'il s'agit d'une hypothèse et non d'un fait établi.
6. WHEN un client de l'API sollicite
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/simulation` avec des identifiants existants,
   THE Plateforme SHALL retourner la Simulation_De_Conséquences de la Réforme_Proposée correspondante.
7. IF l'identifiant de Problème_Juridique ou de Réforme_Proposée transmis à
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/simulation` ne correspond à aucune ressource
   existante, THEN THE Plateforme SHALL rejeter la requête, ne retourner aucune simulation et indiquer
   au demandeur une erreur signalant la ressource introuvable.

### Exigence 6 — Détection d'effets pervers et de contradictions

**User Story:** En tant que citoyen, je veux connaître les risques et effets pervers d'une réforme,
afin d'en évaluer les failles avant de voter.

#### Critères d'acceptation

1. WHEN un Utilisateur consulte une Réforme_Proposée, THE Détection_D_Effets_Pervers SHALL identifier
   les risques, effets indésirables et contradictions de cette Réforme_Proposée.
2. THE Détection_D_Effets_Pervers SHALL proposer, pour chaque risque identifié, au moins une
   contre-mesure possible.
3. THE Détection_D_Effets_Pervers SHALL accompagner chaque risque d'un marqueur indiquant qu'il s'agit
   d'un risque identifié et non d'une conséquence certaine.
4. WHERE le contexte documentaire du Pipeline_RAG fournit des Sources pour un risque, THE
   Détection_D_Effets_Pervers SHALL associer ce risque à ses Sources.
5. IF l'étape de récupération documentaire du Pipeline_RAG échoue lors de la
   Détection_D_Effets_Pervers, THEN THE Plateforme SHALL afficher un message indiquant que la détection
   est momentanément indisponible et SHALL s'abstenir de présenter des risques non fondés sur le
   contexte documentaire.

### Exigence 7 — Agents IA contradictoires et conclusion technique

**User Story:** En tant que citoyen, je veux lire des analyses contradictoires spécialisées et une
synthèse technique, afin de disposer d'un éclairage pluraliste sur chaque réforme.

#### Critères d'acceptation

1. WHEN un Utilisateur consulte une Réforme_Proposée, THE Plateforme SHALL produire les analyses des
   Agents_IA_Contradictoires JuristeIA, BudgetIA, ConstitutionIA, ImpactIA, OpposantIA, DéfenseurIA et
   SimulationIA.
2. THE JuristeIA SHALL évaluer la cohérence juridique de la Réforme_Proposée.
3. THE BudgetIA SHALL évaluer les conséquences financières de la Réforme_Proposée.
4. THE ConstitutionIA SHALL évaluer les risques constitutionnels de la Réforme_Proposée.
5. THE ImpactIA SHALL évaluer l'effet de la Réforme_Proposée sur les populations concernées.
6. THE OpposantIA SHALL identifier les failles de la Réforme_Proposée.
7. THE DéfenseurIA SHALL formuler les meilleurs arguments en faveur de la Réforme_Proposée.
8. THE Plateforme SHALL agréger les analyses des Agents_IA_Contradictoires en une Conclusion_Technique.
9. THE Conclusion_Technique SHALL présenter les analyses de manière non prescriptive et SHALL s'abstenir
   de recommander l'adoption ou le rejet de la Réforme_Proposée.
10. IF l'étape de récupération documentaire du Pipeline_RAG échoue pour un Agent_IA_Contradictoire,
    THEN THE Plateforme SHALL indiquer que l'analyse de cet Agent_IA_Contradictoire est momentanément
    indisponible et SHALL produire la Conclusion_Technique à partir des analyses effectivement
    disponibles.
11. WHEN un client de l'API sollicite
    `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/analysis` avec des identifiants existants,
    THE Plateforme SHALL retourner les analyses des Agents_IA_Contradictoires et la
    Conclusion_Technique de la Réforme_Proposée correspondante.
12. IF l'identifiant de Problème_Juridique ou de Réforme_Proposée transmis à
    `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/analysis` ne correspond à aucune ressource
    existante, THEN THE Plateforme SHALL rejeter la requête, ne retourner aucune analyse et indiquer au
    demandeur une erreur signalant la ressource introuvable.

### Exigence 8 — Vote citoyen proposition par proposition

**User Story:** En tant qu'Utilisateur_Authentifié, je veux voter réforme par réforme et agir sur
chacune, afin de participer à la délibération de façon fine.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié approuve une Réforme_Proposée, THE Plateforme SHALL enregistrer un
   Vote_Citoyen de valeur +1 via le modèle `Vote` et afficher une confirmation dans un délai maximum de
   2 secondes.
2. WHEN un Utilisateur_Authentifié rejette une Réforme_Proposée, THE Plateforme SHALL enregistrer un
   Vote_Citoyen de valeur -1 via le modèle `Vote` et afficher une confirmation dans un délai maximum de
   2 secondes.
3. WHEN un Utilisateur_Authentifié annule sa prise de position sur une Réforme_Proposée, THE Plateforme
   SHALL enregistrer un Vote_Citoyen de valeur 0 via le modèle `Vote`.
4. THE Plateforme SHALL garantir l'unicité d'un Vote_Citoyen par (proposal_id, user_id) conformément au
   modèle `Vote` existant, de sorte qu'un nouveau vote du même Utilisateur_Authentifié sur la même
   Réforme_Proposée remplace la valeur précédente sans créer d'enregistrement supplémentaire.
5. WHEN un Utilisateur_Authentifié demande une explication sur une Réforme_Proposée, THE Assistant_IA
   SHALL fournir une réponse documentée via le Pipeline_RAG accompagnée d'au moins une Source citée.
6. IF l'Assistant_IA ne peut associer aucune Source à la réponse via le Pipeline_RAG, THEN THE
   Plateforme SHALL s'abstenir de présenter une réponse et afficher un message indiquant l'absence de
   Source documentée, sans altérer le Vote_Citoyen existant.
7. WHEN un Utilisateur_Authentifié propose un Amendement à une Réforme_Proposée, THE Plateforme SHALL
   enregistrer une nouvelle Version_De_Proposition conformément à l'Exigence 10.
8. WHEN un Utilisateur_Authentifié signale un effet secondaire d'une Réforme_Proposée, THE Plateforme
   SHALL enregistrer un Signalement_D_Effet_Secondaire rattaché à la Réforme_Proposée.
9. IF le texte d'un Amendement ou d'un Signalement_D_Effet_Secondaire est vide ou dépasse 5000
   caractères, THEN THE Plateforme SHALL refuser l'enregistrement, conserver la saisie de
   l'Utilisateur_Authentifié et afficher un message indiquant la contrainte de longueur non respectée.
10. IF un Utilisateur non authentifié tente d'approuver, de rejeter, d'annuler une prise de position, de
    proposer un Amendement ou de signaler un effet secondaire, THEN THE Plateforme SHALL refuser
    l'action, laisser inchangé tout Vote_Citoyen existant et inviter à l'authentification.

### Exigence 9 — Décomptes et restitution des votes

**User Story:** En tant que visiteur, je veux voir le soutien de chaque réforme à travers des décomptes
explicites, afin d'apprécier la participation sans être orienté par un simple classement.

#### Critères d'acceptation

1. THE Plateforme SHALL calculer, pour chaque Réforme_Proposée, support_count, oppose_count et
   participation_count via le service `popularity_service` existant, avec participation_count égal à la
   somme de support_count et oppose_count.
2. WHEN un Vote_Citoyen est enregistré ou retiré pour une Réforme_Proposée, THE Plateforme SHALL
   recalculer support_count, oppose_count et participation_count de cette Réforme_Proposée.
3. WHERE un pourcentage de soutien est affiché, THE Interface_Publique SHALL afficher également le
   nombre absolu de Votes_Citoyens correspondant.
4. THE Plateforme SHALL exposer les décomptes de Votes_Citoyens d'une Réforme_Proposée via
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/votes`, en retournant support_count,
   oppose_count et participation_count.
5. IF l'identifiant de Problème_Juridique ou de Réforme_Proposée transmis à
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/votes` ne correspond à aucune ressource
   existante, THEN THE Plateforme SHALL rejeter la requête, ne retourner aucun décompte, et indiquer au
   demandeur une erreur signalant la ressource introuvable.
6. THE Plateforme SHALL s'abstenir de classer les Réformes_Proposées par un simple
   `ORDER BY vote_count`, et SHALL utiliser le score de popularité produit par le service
   `popularity_service` lorsqu'un ordre d'affichage est requis.

### Exigence 10 — Historique de versions de type Git des réformes

**User Story:** En tant que citoyen, je veux suivre l'évolution d'une réforme comme un dépôt Git, afin
de comprendre qui a modifié quoi, pourquoi, et depuis quelle version.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter l'historique d'une Réforme_Proposée au moyen du modèle
   `ProposalVersion` existant.
2. WHEN une Réforme_Proposée est modifiée ou amendée, THE Plateforme SHALL enregistrer une
   Version_De_Proposition complète comportant un `snapshot`, un `change_summary` et l'auteur de la
   modification (`edited_by`), et SHALL incrémenter le numéro de version d'exactement 1 par rapport à la
   Version_De_Proposition précédente.
3. THE Plateforme SHALL conserver l'intégralité des Versions_De_Proposition d'une Réforme_Proposée sans
   jamais écraser une version antérieure.
4. WHEN un client de l'API sollicite
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/versions` avec des identifiants existants,
   THE Plateforme SHALL retourner l'historique des Versions_De_Proposition de la Réforme_Proposée
   correspondante, ordonné par numéro de version croissant.
5. IF l'identifiant de Problème_Juridique ou de Réforme_Proposée transmis à
   `GET /api/v1/legal-problems/{id}/reforms/{reform_id}/versions` ne correspond à aucune ressource
   existante, THEN THE Plateforme SHALL rejeter la requête, ne retourner aucun historique et indiquer au
   demandeur une erreur signalant la ressource introuvable.
6. WHEN un Utilisateur consulte l'historique d'une Réforme_Proposée, THE Interface_Publique SHALL
   afficher, pour chaque Version_De_Proposition, l'auteur de la modification, le résumé de modification
   (`change_summary`) et le numéro de la version précédente.

### Exigence 11 — Rôle non-décisionnel et neutralité de l'IA

**User Story:** En tant qu'exploitant de la Plateforme, je veux que l'IA reste un outil de compréhension
et d'organisation, afin qu'elle n'oriente pas les choix des citoyens.

#### Critères d'acceptation

1. THE Assistant_IA SHALL s'abstenir de produire une conclusion affirmant qu'une Réforme_Proposée doit
   être adoptée ou doit être rejetée.
2. THE Assistant_IA SHALL s'abstenir de formuler une recommandation de vote, telle que « votez pour »,
   « votez contre » ou « je recommande d'approuver cette réforme ».
3. WHERE une question porte sur un choix entre Réformes_Proposées, THE Assistant_IA SHALL présenter les
   arguments documentés de chaque Réforme_Proposée sans inciter l'Utilisateur à voter dans un sens donné.
4. WHEN les analyses de DéfenseurIA et d'OpposantIA sont produites pour une Réforme_Proposée, THE
   Assistant_IA SHALL faire en sorte que l'écart entre le nombre d'arguments produits par DéfenseurIA et
   le nombre d'arguments produits par OpposantIA n'excède pas 1.
5. IF une Réforme_Proposée ne dispose d'aucun argument documenté pour le rôle DéfenseurIA ou pour le
   rôle OpposantIA, THEN THE Assistant_IA SHALL indiquer l'absence d'argument documenté pour ce rôle et
   SHALL s'abstenir de fabriquer un argument non fondé sur le contexte documentaire.
6. WHEN l'Assistant_IA produit une analyse, THE Assistant_IA SHALL distinguer les faits, les
   estimations, les opinions, les hypothèses et les désaccords entre Sources au moyen d'un marqueur de
   catégorie visible associé à chaque énoncé concerné.

### Exigence 12 — Transparence des sources juridiques et anti-hallucination

**User Story:** En tant que citoyen, je veux vérifier l'origine juridique de chaque affirmation, afin de
faire confiance aux analyses sans risque de manipulation.

#### Critères d'acceptation

1. THE Assistant_IA SHALL exiger une étape de récupération documentaire (retrieval) du Pipeline_RAG
   avant toute génération d'Analyse_Juridique, de Simulation_De_Conséquences, de
   Détection_D_Effets_Pervers ou d'analyse d'Agent_IA_Contradictoire.
2. THE Assistant_IA SHALL générer ces analyses à une température comprise entre 0.0 et 0.3
   inclusivement.
3. THE Assistant_IA SHALL exiger des citations de Sources pour chaque Affirmation_Juridique et SHALL
   vérifier que chaque citation produite renvoie à une Source présente dans le contexte documentaire du
   Pipeline_RAG.
4. WHEN une Affirmation_Juridique n'est associée à aucune Source, THE Assistant_IA SHALL détecter cette
   Affirmation_Juridique non sourcée et SHALL s'abstenir de la présenter comme établie.
5. THE Plateforme SHALL afficher les Sources_Juridiques citées avec un lien renvoyant à Légifrance, au
   texte réglementaire ou à la décision de jurisprudence correspondante.
6. THE Plateforme SHALL journaliser chaque analyse produite par le Pipeline_RAG pour cette
   fonctionnalité.

### Exigence 13 — Réutilisation des fondations existantes

**User Story:** En tant qu'exploitant de la Plateforme, je veux que « Réparer la loi » réutilise les
modèles et services existants, afin d'éviter la duplication et de préserver la cohérence.

#### Critères d'acceptation

1. WHEN une Réforme_Proposée est créée, THE Plateforme SHALL la persister au moyen d'une instance du
   modèle `Proposal` existant, sans introduire de modèle équivalent ni de table distincte.
2. WHEN un Vote_Citoyen est soumis pour une Réforme_Proposée, THE Plateforme SHALL l'enregistrer au
   moyen du modèle `Vote` existant, en le rattachant à l'instance `Proposal` correspondante.
3. WHEN une prise de position est soumise pour une Réforme_Proposée, THE Plateforme SHALL l'enregistrer
   au moyen du modèle `Argument` existant avec une position appartenant à l'ensemble {FOR, AGAINST}.
4. IF une prise de position est soumise avec une position hors de l'ensemble {FOR, AGAINST}, THEN THE
   Plateforme SHALL rejeter la soumission, conserver l'état inchangé de la Réforme_Proposée et retourner
   une indication d'erreur précisant que la position est invalide.
5. WHEN une modification d'une Réforme_Proposée est enregistrée, THE Plateforme SHALL créer une nouvelle
   entrée au moyen du modèle `ProposalVersion` existant en préservant les versions antérieures.
6. WHEN une Source_Juridique est rattachée à une Réforme_Proposée, THE Plateforme SHALL la représenter
   au moyen des modèles `Source` et `Document` existants et la traiter au moyen du Pipeline_D_Ingestion
   existant.
7. IF le traitement d'une Source_Juridique par le Pipeline_D_Ingestion échoue, THEN THE Plateforme SHALL
   conserver l'état inchangé de la Réforme_Proposée et retourner une indication d'erreur signalant
   l'échec du rattachement de la source.
8. THE Plateforme SHALL réaliser les opérations sur les Réformes_Proposées, Votes_Citoyens, prises de
   position, versions, sources, thèmes, statistiques et modération exclusivement au moyen des services
   `proposal_service`, `vote_service`, `argument_service`, `source_service`, `theme_service`,
   `statistics_service` et `moderation_service` existants, sans introduire de service redondant assurant
   une responsabilité déjà couverte par l'un de ces services.

### Exigence 14 — API REST « Réparer la loi »

**User Story:** En tant que client de l'API, je veux accéder aux entités de « Réparer la loi » via des
points d'accès REST versionnés, afin d'alimenter le frontend et d'éventuelles intégrations.

#### Critères d'acceptation

1. WHEN un client de l'API sollicite la liste d'une collection sous `/api/v1/legal-problems`
   (Problèmes_Juridiques, Réformes_Proposées ou sous-ressources listables), THE API SHALL retourner une
   réponse paginée avec une taille de page par défaut de 20, une taille de page maximale de 100 et le
   nombre total d'éléments disponibles.
2. WHEN un client de l'API sollicite le détail d'un Problème_Juridique, d'une Réforme_Proposée ou d'une
   sous-ressource existante sous `/api/v1/legal-problems`, THE API SHALL retourner la représentation de
   la ressource demandée.
3. IF une requête sous `/api/v1/legal-problems` désigne un Problème_Juridique, une Réforme_Proposée ou
   une sous-ressource inexistant, THEN THE API SHALL rejeter la requête, laisser l'état de la Plateforme
   inchangé et retourner une erreur signalant la ressource introuvable.
4. IF une requête modifiant l'état (vote, amendement, signalement) est reçue sans authentification
   valide, THEN THE API SHALL refuser la requête, laisser l'état de la Plateforme inchangé et retourner
   une erreur d'authentification.
5. IF une requête modifiant l'état est reçue avec un corps invalide, THEN THE API SHALL refuser la
   requête, laisser l'état de la Plateforme inchangé et retourner une erreur de validation énumérant les
   échecs observables (champ obligatoire absent, type de champ incorrect, valeur hors bornes) et
   identifiant chaque champ en erreur.

### Exigence 15 — Modération et signalements

**User Story:** En tant qu'exploitant de la Plateforme, je veux modérer les amendements et signalements,
afin de préserver la qualité du débat.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié soumet un Amendement textuel ou un Signalement_D_Effet_Secondaire,
   THE Moteur_De_Modération SHALL appliquer le filtre automatique et la classification existants dans un
   délai maximum de 5 secondes et produire exactement l'une des trois décisions suivantes :
   « acceptable », « douteux » ou « interdit ».
2. IF la classification conclut « douteux », THEN THE Moteur_De_Modération SHALL placer la contribution
   dans la File_De_Modération avec le statut « en attente de révision » et SHALL empêcher sa publication
   publique jusqu'à décision d'un modérateur.
3. THE Plateforme SHALL consigner dans le Journal_D_Audit existant, dans un délai maximum de 5 secondes
   après chaque décision de modération, une entrée contenant l'identifiant de la contribution, la
   décision retenue, l'horodatage et l'origine de la décision (automatique ou modérateur).
4. IF la classification conclut « acceptable », THEN THE Moteur_De_Modération SHALL publier la
   contribution immédiatement et SHALL en notifier l'auteur.
5. IF la classification conclut « interdit », THEN THE Moteur_De_Modération SHALL rejeter la contribution
   sans la publier, SHALL en conserver le contenu soumis pour révision, et SHALL notifier l'auteur par
   un message indiquant le rejet et son motif de catégorie.
6. IF le Moteur_De_Modération est indisponible ou échoue lors du traitement d'une contribution soumise,
   THEN THE Plateforme SHALL placer la contribution dans la File_De_Modération avec le statut « en
   attente de révision », SHALL empêcher sa publication publique, et SHALL consigner l'échec dans le
   Journal_D_Audit.

### Exigence 16 — Restitution des pages « Réparer la loi » (SSR)

**User Story:** En tant que visiteur, je veux consulter les pages de « Réparer la loi » rendues côté
serveur, afin de comprendre chaque problème et chaque réforme sans authentification.

#### Critères d'acceptation

1. THE Plateforme SHALL rendre les pages de la Rubrique_Réparer_La_Loi côté serveur en produisant le
   HTML complet de la page avant envoi au navigateur, en utilisant Jinja2, HTMX, Alpine.js et Tailwind.
2. WHEN un visiteur ouvre la page d'un Problème_Juridique, THE Interface_Publique SHALL afficher
   l'Analyse_Juridique, les Réformes_Proposées comparées, la Simulation_De_Conséquences, la
   Détection_D_Effets_Pervers, les analyses des Agents_IA_Contradictoires, la Conclusion_Technique, les
   décomptes de Votes_Citoyens et les Sources_Juridiques citées.
3. IF une des sections attendues d'un Problème_Juridique ne contient aucune donnée, THEN THE
   Interface_Publique SHALL afficher pour cette section un libellé visible indiquant l'absence de
   contenu, tout en rendant les autres sections disponibles.
4. WHEN la page d'un Problème_Juridique est affichée, THE Interface_Publique SHALL présenter, pour
   chaque Réforme_Proposée, les actions Approuver, Rejeter, Proposer un amendement, Demander une
   explication et Signaler un effet secondaire.
5. WHERE une valeur affichée est une estimation ou une hypothèse, THE Interface_Publique SHALL
   accompagner cette valeur d'un marqueur textuel visible indiquant qu'il s'agit d'une estimation ou
   d'une hypothèse.
6. WHEN un visiteur non authentifié ouvre une page de consultation de la Rubrique_Réparer_La_Loi, THE
   Interface_Publique SHALL afficher le contenu de la page sans requérir d'authentification.
7. IF le Problème_Juridique demandé est introuvable ou si ses données ne peuvent être chargées, THEN THE
   Interface_Publique SHALL afficher un message indiquant que le contenu est indisponible sans exposer
   de détails techniques internes.

### Exigence 17 — Évolutions futures hors périmètre du MVP

**User Story:** En tant qu'exploitant de la Plateforme, je veux délimiter clairement les évolutions
futures, afin de concentrer le MVP sur la délibération réforme par réforme.

#### Critères d'acceptation

1. THE Plateforme SHALL s'abstenir d'exposer, dans la Rubrique_Réparer_La_Loi, tout élément d'interface
   ou toute fonction donnant accès au tableau de bord budgétaire global « Où va l'argent ? ».
2. THE Plateforme SHALL s'abstenir d'exposer, dans la Rubrique_Réparer_La_Loi, tout élément d'interface
   ou toute fonction donnant accès au « programme citoyen complet » agrégeant l'ensemble des
   Réformes_Proposées.
3. THE Plateforme SHALL s'abstenir de fournir toute fonction construisant automatiquement un Programme
   définitif à partir des Votes_Citoyens.
4. IF un Utilisateur tente d'accéder à une fonctionnalité exclue mentionnée aux critères 1 à 3, THEN THE
   Plateforme SHALL laisser inchangé l'état des données et s'abstenir d'exécuter l'action demandée.
5. THE Plateforme SHALL organiser la délibération une Réforme_Proposée par unité de délibération, sans
   agréger les Votes_Citoyens de plusieurs Réformes_Proposées en une décision unique.
