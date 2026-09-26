# Requirements Document

## Introduction

Cette fonctionnalité ajoute deux membres fondateurs à la page publique « Équipes » (`/equipes`) de PARTIPOLAI. Les fondateurs doivent apparaître dans une Équipe nommée « Fondateurs » sans nécessiter de compte Utilisateur associé. Le modèle `TeamMember` est actuellement lié à un Utilisateur via `user_id` non nul, et le gabarit `equipes.html` affiche « Utilisateur #{user_id} » ; ces contraintes empêchent de présenter des personnes qui n'ont pas de compte sur la Plateforme.

Pour répondre à ce besoin, `TeamMember` est étendu avec des champs d'affichage (nom, chemin de photo, URL LinkedIn) et `user_id` devient facultatif. Une Équipe « Fondateurs » et ses deux membres sont insérés par migration Alembic. La photo de Samuel Lepetre est stockée dans les ressources statiques de l'application ; Nael Lepetre, sans photo, est présenté avec un substitut (initiales).

Données concrètes à insérer :
- Équipe : « Fondateurs »
- Samuel Lepetre — LinkedIn : `https://www.linkedin.com/in/samuel-lepetre%F0%9F%8F%89-90010713/` — avec photo
- Nael Lepetre — sans LinkedIn, sans photo

## Glossary

- **Plateforme**: L'application PARTIPOLAI (FastAPI + SQLAlchemy 2.x async + Jinja2 SSR + Alembic).
- **Page_Equipes**: La page publique en rendu serveur servie sur `/equipes`, produite par le gabarit `app/templates/equipes.html`.
- **Membre_Fondateur**: Un `TeamMember` de l'Équipe « Fondateurs » présenté par ses champs d'affichage (nom, photo, LinkedIn) plutôt que par un compte Utilisateur.
- **Champs_Affichage**: Les colonnes ajoutées au modèle `TeamMember` : nom affiché (`display_name`), chemin de photo (`photo_path`) et URL LinkedIn (`linkedin_url`).
- **Substitut_Initiales**: Un élément visuel de repli affiché à la place d'une photo, dérivé des initiales du nom affiché du Membre_Fondateur.
- **Migration_Fondateurs**: La migration Alembic qui crée l'Équipe « Fondateurs » et insère les deux Membre_Fondateur.
- **Photo_Statique**: Le fichier image de Samuel Lepetre stocké sous `app/static/img/team/` et référencé par `photo_path`.

## Requirements

### Requirement 1

**User Story:** En tant que responsable de la Plateforme, je veux que le modèle `TeamMember` porte des champs d'affichage, afin qu'un membre puisse être présenté sans compte Utilisateur.

#### Acceptance Criteria

1. THE Plateforme SHALL exposer sur `TeamMember` une colonne `display_name` de type texte d'une longueur maximale de 255 caractères.
2. THE Plateforme SHALL exposer sur `TeamMember` une colonne `photo_path` de type texte acceptant la valeur nulle.
3. THE Plateforme SHALL exposer sur `TeamMember` une colonne `linkedin_url` de type texte acceptant la valeur nulle.
4. THE Plateforme SHALL définir la colonne `user_id` de `TeamMember` comme acceptant la valeur nulle.
5. WHERE un `TeamMember` possède une valeur `user_id` non nulle, THE Plateforme SHALL conserver la contrainte de clé étrangère référençant la table `users`.

### Requirement 2

**User Story:** En tant que visiteur, je veux voir le nom, la photo et le lien LinkedIn des fondateurs sur la Page_Equipes, afin d'identifier les personnes à l'origine du projet.

#### Acceptance Criteria

1. WHEN un visiteur consulte la Page_Equipes, THE Page_Equipes SHALL afficher le contenu de `display_name` de chaque Membre_Fondateur à la place de l'identifiant Utilisateur.
2. WHERE un Membre_Fondateur possède une valeur `photo_path` non nulle, THE Page_Equipes SHALL afficher l'image référencée par `photo_path`.
3. WHERE un Membre_Fondateur possède une valeur `photo_path` nulle, THE Page_Equipes SHALL afficher un Substitut_Initiales dérivé du `display_name`.
4. WHERE un Membre_Fondateur possède une valeur `linkedin_url` non nulle, THE Page_Equipes SHALL afficher un lien pointant vers l'URL `linkedin_url`.
5. WHERE un Membre_Fondateur possède une valeur `linkedin_url` nulle, THE Page_Equipes SHALL afficher le membre sans lien LinkedIn.
6. WHEN un visiteur consulte la Page_Equipes, THE Page_Equipes SHALL rester consultable sans authentification.

### Requirement 3

**User Story:** En tant que responsable de la Plateforme, je veux que l'Équipe « Fondateurs » et ses membres soient créés par migration, afin qu'ils apparaissent sur la Page_Equipes après déploiement.

#### Acceptance Criteria

1. WHEN la Migration_Fondateurs est appliquée, THE Plateforme SHALL créer une Équipe dont le champ `name` vaut « Fondateurs ».
2. WHEN la Migration_Fondateurs est appliquée, THE Plateforme SHALL insérer un Membre_Fondateur dont le `display_name` vaut « Samuel Lepetre », dont le `photo_path` référence la Photo_Statique, et dont le `linkedin_url` vaut `https://www.linkedin.com/in/samuel-lepetre%F0%9F%8F%89-90010713/`.
3. WHEN la Migration_Fondateurs est appliquée, THE Plateforme SHALL insérer un Membre_Fondateur dont le `display_name` vaut « Nael Lepetre », dont le `photo_path` est nul, et dont le `linkedin_url` est nul.
4. THE Plateforme SHALL insérer les deux Membre_Fondateur avec une valeur `user_id` nulle.
5. WHEN la Migration_Fondateurs est annulée (downgrade), THE Plateforme SHALL supprimer les deux Membre_Fondateur et l'Équipe « Fondateurs » créés à l'étape upgrade.

### Requirement 4

**User Story:** En tant que visiteur, je veux que la photo du fondateur soit servie par la Plateforme, afin qu'elle s'affiche de façon fiable sur la Page_Equipes.

#### Acceptance Criteria

1. THE Plateforme SHALL stocker la Photo_Statique dans le répertoire `app/static/img/team/`.
2. WHEN un visiteur charge la Page_Equipes contenant Samuel Lepetre, THE Plateforme SHALL servir la Photo_Statique via la route des ressources statiques.

### Requirement 5

**User Story:** En tant que mainteneur, je veux que les schémas et le service des Équipes exposent les champs d'affichage, afin que la Page_Equipes et l'API restituent des données cohérentes.

#### Acceptance Criteria

1. THE Plateforme SHALL inclure les champs `display_name`, `photo_path` et `linkedin_url` dans la représentation publique d'un membre d'Équipe.
2. THE Plateforme SHALL définir le champ `user_id` de la représentation publique d'un membre d'Équipe comme acceptant la valeur nulle.
3. WHEN la méthode `list_teams` retourne l'Équipe « Fondateurs », THE Plateforme SHALL inclure les deux Membre_Fondateur avec leurs Champs_Affichage renseignés.
