# Design Document

## Introduction

Cette conception décrit l'ajout de deux Membre_Fondateur à la Page_Equipes
(`/equipes`) sans compte Utilisateur. Le modèle `TeamMember` gagne des
Champs_Affichage (`display_name`, `photo_path`, `linkedin_url`) et sa colonne
`user_id` devient facultative. Une Migration_Fondateurs crée l'Équipe
« Fondateurs » et insère Samuel Lepetre (avec Photo_Statique et LinkedIn) et
Nael Lepetre (sans photo ni LinkedIn). Les schémas Pydantic et le gabarit SSR
sont mis à jour pour restituer ces champs, avec un Substitut_Initiales quand la
photo est absente.

La conception reste alignée sur l'architecture existante : SQLAlchemy 2.x async
(style `Mapped`), Alembic pour le schéma et l'amorçage de données, schémas
Pydantic v2 (`from_attributes`), rendu Jinja2 côté serveur via `TeamService` et
la route publique `/equipes`.

## Architecture

Le flux de bout en bout est inchangé dans sa structure ; seuls les champs
transportés s'enrichissent.

```
Migration Alembic (schéma + seed)
        │
        ▼
   team_members  ──(ORM: TeamMember)──▶  TeamService.list_teams()
        ▲                                       │
   teams (Fondateurs)                           ▼
                                    TeamPublic / TeamMemberPublic (Pydantic)
                                                │
                                                ▼
                           equipes.html (SSR) : nom, photo|initiales, LinkedIn
                                                │
                                                ▼
                          app/static/img/team/<photo>  (StaticFiles)
```

Deux migrations distinctes sont préférées à une seule pour séparer le changement
de schéma (réversible par DDL) de l'amorçage de données (réversible par
suppression ciblée) :

1. **Migration de schéma** — ajoute les colonnes `display_name`, `photo_path`,
   `linkedin_url` et rend `user_id` nullable.
2. **Migration_Fondateurs** — insère l'Équipe « Fondateurs » et ses deux membres.

Chaînage des révisions : `0002_user_consents` → `0003_team_member_display_fields`
→ `0004_seed_founding_members`.

## Components and Interfaces

### 1. Modèle ORM — `app/models/team.py`

`TeamMember` est étendu. `user_id` passe nullable ; les Champs_Affichage sont
ajoutés ; la relation `user` devient optionnelle.

```python
class TeamMember(TimestampMixin, Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # user_id devient facultatif (Exigence 1.4) ; la FK vers users est conservée
    # lorsqu'une valeur est présente (Exigence 1.5).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Exigence 1.1
    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)     # Exigence 1.2
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)   # Exigence 1.3
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    team: Mapped["Team"] = relationship("Team", back_populates="members", lazy="selectin")
    user: Mapped["User | None"] = relationship("User", lazy="selectin")
```

Note sur 1.1 : la spécification impose une longueur maximale de 255 caractères
pour `display_name`. `photo_path` et `linkedin_url` sont des textes libres ;
`String(512)` est retenu comme borne raisonnable pour des chemins/URL (le critère
n'impose qu'une nullabilité, pas de longueur).

### 2. Migration de schéma — `alembic/versions/0003_team_member_display_fields.py`

```python
def upgrade() -> None:
    op.add_column("team_members", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("team_members", sa.Column("photo_path", sa.String(512), nullable=True))
    op.add_column("team_members", sa.Column("linkedin_url", sa.String(512), nullable=True))
    op.alter_column("team_members", "user_id", existing_type=sa.Integer(), nullable=True)

def downgrade() -> None:
    # Restaure user_id NON NULL avant de retirer les colonnes d'affichage.
    op.alter_column("team_members", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("team_members", "linkedin_url")
    op.drop_column("team_members", "photo_path")
    op.drop_column("team_members", "display_name")
```

La FK `fk_team_members_user_id_users` n'est pas touchée : rendre la colonne
nullable ne supprime pas la contrainte, qui continue de s'appliquer aux valeurs
non nulles (Exigence 1.5).

### 3. Migration_Fondateurs — `alembic/versions/0004_seed_founding_members.py`

Amorçage par instructions SQL Core (indépendantes de l'ORM, robustes aux
évolutions de modèle). Constantes partagées entre `upgrade` et `downgrade` pour
un ciblage exact au rollback (Exigence 3.5).

```python
_TEAM_NAME = "Fondateurs"
_PHOTO_PATH = "img/team/samuel-lepetre.jpg"   # relatif au montage StaticFiles
_LINKEDIN_SAMUEL = "https://www.linkedin.com/in/samuel-lepetre%F0%9F%8F%89-90010713/"
_MEMBER_NAMES = ("Samuel Lepetre", "Nael Lepetre")

def upgrade() -> None:
    conn = op.get_bind()
    team_id = conn.execute(
        sa.text("INSERT INTO teams (name, created_at, updated_at) "
                "VALUES (:name, now(), now()) RETURNING id"),
        {"name": _TEAM_NAME},
    ).scalar_one()

    conn.execute(
        sa.text(
            "INSERT INTO team_members "
            "(team_id, user_id, display_name, photo_path, linkedin_url, created_at, updated_at) "
            "VALUES "
            "(:tid, NULL, :n1, :p1, :l1, now(), now()), "
            "(:tid, NULL, :n2, NULL, NULL, now(), now())"
        ),
        {"tid": team_id, "n1": "Samuel Lepetre", "p1": _PHOTO_PATH,
         "l1": _LINKEDIN_SAMUEL, "n2": "Nael Lepetre"},
    )

def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM team_members WHERE display_name IN :names AND team_id IN "
            "(SELECT id FROM teams WHERE name = :team)"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(_MEMBER_NAMES), "team": _TEAM_NAME},
    )
    conn.execute(sa.text("DELETE FROM teams WHERE name = :team"), {"team": _TEAM_NAME})
```

Le downgrade cible les deux membres par `display_name` **et** par appartenance à
l'Équipe « Fondateurs », puis supprime l'Équipe, restituant l'état antérieur
(Exigence 3.5).

### 4. Schémas Pydantic — `app/schemas/team.py`

```python
class TeamMemberPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: int
    user_id: int | None = None            # Exigence 5.2
    display_name: str | None = None       # Exigence 5.1
    photo_path: str | None = None         # Exigence 5.1
    linkedin_url: str | None = None       # Exigence 5.1
    role: str | None = None
    bio: str | None = None
    created_at: datetime
    updated_at: datetime


class TeamMemberCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: int | None = Field(default=None, gt=0)
    display_name: str | None = Field(default=None, max_length=255)
    photo_path: str | None = Field(default=None, max_length=512)
    linkedin_url: str | None = Field(default=None, max_length=512)
    role: str | None = Field(default=None, max_length=120)
    bio: str | None = None
```

`TeamPublic` reste inchangé (il agrège `TeamMemberPublic`). `list_teams`
n'est pas modifié : grâce au chargement `selectin`, l'Équipe « Fondateurs »
et ses membres (avec Champs_Affichage) sont retournés tels quels (Exigence 5.3).

### 5. Gabarit SSR — `app/templates/equipes.html`

La colonne « Membre » remplace « Utilisateur #{user_id} » par un rendu riche :

- **Nom** : `display_name` s'il est présent, sinon repli sur `Utilisateur #user_id`
  pour préserver les Équipes existantes liées à des comptes (Exigence 2.1).
- **Photo** : si `photo_path` est non nul, `<img src="{{ url_for('static', path=member.photo_path) }}" alt="{{ display_name }}">`
  (Exigences 2.2, 4.2) ; sinon un Substitut_Initiales `<span>` affichant les
  initiales (Exigence 2.3).
- **LinkedIn** : si `linkedin_url` est non nul, un `<a href="{{ member.linkedin_url }}" rel="noopener" target="_blank">`
  (Exigence 2.4) ; sinon rien (Exigence 2.5).

Le calcul des initiales est fait dans le gabarit à partir des mots de
`display_name` (première lettre des deux premiers mots, en majuscules). Un macro
Jinja `initials(name)` isole cette logique pure pour la rendre testable.

```jinja
{% macro initials(name) -%}
  {%- set parts = (name or "").split() -%}
  {{- (parts[0][:1] ~ (parts[1][:1] if parts|length > 1 else "")) | upper -}}
{%- endmacro %}
```

Le rendu conserve les `data-testid` existants (`team`, `team-member`) et
l'accessibilité (`alt` sur l'image, texte de lien explicite). La page reste
servie par la route publique `/equipes` sans authentification (Exigence 2.6) —
aucun changement de routage.

### 6. Ressource statique — `app/static/img/team/`

Le répertoire `app/static/img/team/` est créé et la Photo_Statique de Samuel y
est déposée (Exigence 4.1). Le montage `StaticFiles` existant (`/static`) sert le
fichier ; `photo_path` stocke le chemin relatif au montage (`img/team/...`) de
sorte que `url_for('static', path=member.photo_path)` produise l'URL servie
(Exigence 4.2).

## Data Models

| Colonne (`team_members`) | Type | Null | Notes |
|--------------------------|------|------|-------|
| `id` | Integer PK | non | inchangé |
| `team_id` | Integer FK → teams.id | non | inchangé |
| `user_id` | Integer FK → users.id | **oui** | devient nullable (Ex. 1.4/1.5) |
| `display_name` | String(255) | oui | Ex. 1.1 |
| `photo_path` | String(512) | oui | Ex. 1.2 |
| `linkedin_url` | String(512) | oui | Ex. 1.3 |
| `role` | String(120) | oui | inchangé |
| `bio` | Text | oui | inchangé |
| `created_at`/`updated_at` | DateTime(tz) | non | via `TimestampMixin` |

Données amorcées (Équipe « Fondateurs ») :

| display_name | user_id | photo_path | linkedin_url |
|--------------|---------|-----------|--------------|
| Samuel Lepetre | NULL | `img/team/samuel-lepetre.jpg` | `https://www.linkedin.com/in/samuel-lepetre%F0%9F%8F%89-90010713/` |
| Nael Lepetre | NULL | NULL | NULL |

## Error Handling

- **Membre sans nom et sans compte** : le gabarit se replie sur une chaîne vide
  d'initiales et n'affiche pas d'identifiant Utilisateur inexistant ; ce cas ne
  survient pas pour les données amorcées mais le rendu reste robuste.
- **Photo manquante à l'exécution** : si le fichier référencé par `photo_path`
  est absent, `StaticFiles` renvoie 404 pour l'image uniquement ; la page reste
  rendue. Le test de placement (Exigence 4.1) prévient ce cas pour Samuel.
- **Downgrade partiel** : la Migration_Fondateurs supprime d'abord les membres
  puis l'Équipe, en ciblant par nom, ce qui évite de toucher d'autres Équipes ou
  membres et laisse le schéma intact pour la migration de schéma qui suit.
- **Validation d'entrée API** : `TeamMemberCreate` borne les longueurs
  (`display_name` ≤ 255, `photo_path`/`linkedin_url` ≤ 512) ; les entrées hors
  bornes sont rejetées par Pydantic (422).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Le nom affiché remplace l'identifiant Utilisateur

*For any* Membre_Fondateur possédant un `display_name` non vide, le rendu de la
Page_Equipes contient ce `display_name` et ne contient pas la forme
« Utilisateur #… » pour ce membre.

**Validates: Requirements 2.1**

### Property 2: Photo ou substitut, jamais les deux

*For any* membre rendu sur la Page_Equipes, si `photo_path` est non nul alors le
rendu contient une balise image référençant ce chemin ; si `photo_path` est nul
alors le rendu contient le Substitut_Initiales et aucune balise image pour ce
membre.

**Validates: Requirements 2.2, 2.3**

### Property 3: Dérivation déterministe des initiales

*For any* `display_name`, la fonction de dérivation d'initiales produit un
résultat déterministe composé des premières lettres (en majuscules) des deux
premiers mots du nom, et le même nom produit toujours les mêmes initiales.

**Validates: Requirements 2.3**

### Property 4: Présence conditionnelle du lien LinkedIn

*For any* membre rendu sur la Page_Equipes, le rendu contient un lien dont l'URL
est exactement `linkedin_url` si et seulement si `linkedin_url` est non nul.

**Validates: Requirements 2.4, 2.5**

### Property 5: Aller-retour des Champs_Affichage dans la représentation publique

*For any* `TeamMember` porteur de `display_name`, `photo_path`, `linkedin_url` et
d'un `user_id` éventuellement nul, valider ce membre en `TeamMemberPublic` puis le
sérialiser restitue les mêmes valeurs pour ces quatre champs.

**Validates: Requirements 5.1, 5.2**

## Testing Strategy

Approche double : tests unitaires/exemples pour les faits structurels et les
scénarios précis, tests de propriété pour les règles universelles de rendu et de
sérialisation. Les tests de propriété exécutent au minimum 100 itérations et
référencent la propriété de conception via l'étiquette
**Feature: add-founding-members, Property {number}: {property_text}**.

### Tests de propriété (property-based, ≥ 100 itérations)

- **Property 1** — génère des membres avec `display_name` variés ; rend
  `equipes.html` (ou la macro de ligne) ; vérifie la présence du nom et l'absence
  de « Utilisateur #… ».
- **Property 2** — génère des membres avec/sans `photo_path` ; vérifie la présence
  d'`<img>` référençant le chemin sinon le Substitut_Initiales sans image.
- **Property 3** — génère des noms (mots multiples, accents, mot unique, chaînes
  vides) ; vérifie le déterminisme et la forme des initiales (fonction pure de la
  macro).
- **Property 4** — génère des membres avec/sans `linkedin_url` ; vérifie la
  présence conditionnelle d'un `<a href>` exactement égal à l'URL.
- **Property 5** — génère des `TeamMember` (dont `user_id=None`) ; vérifie que
  `TeamMemberPublic.model_validate(m).model_dump()` restitue les Champs_Affichage
  et accepte `user_id` nul.

Générateurs : noms unicode (accents, emoji comme dans l'URL LinkedIn), chemins de
photo plausibles, URL LinkedIn. Cas limites couverts par les générateurs : nom à
un seul mot, nom vide, `user_id` nul.

### Tests d'exemple / unitaires

- **1.1–1.5, 5.2** — introspection du modèle `TeamMember` : présence et type des
  colonnes `display_name` (String 255), `photo_path`/`linkedin_url` (nullable),
  `user_id` nullable, FK vers `users` conservée ; instanciation avec `user_id=None`.
- **2.6** — `GET /equipes` sans authentification renvoie 200 et rend la liste.

### Tests d'intégration (base de données réelle, migrations)

- **3.1–3.5** — appliquer les migrations puis vérifier : Équipe « Fondateurs »
  créée ; Samuel (photo + LinkedIn), Nael (nuls), `user_id` nul pour les deux ;
  après `downgrade`, l'Équipe et ses deux membres ont disparu.
- **4.1** — la Photo_Statique existe sous `app/static/img/team/` et `photo_path`
  pointe dans ce répertoire (smoke).
- **4.2** — `GET` de l'URL statique de la photo renvoie 200 avec un type image.
- **5.3** — après migration, `TeamService.list_teams()` retourne l'Équipe
  « Fondateurs » dont les deux membres portent leurs Champs_Affichage.

### Fichiers de test impactés

- `tests/unit/test_team_service.py` — la `_FakeSession` et les assertions
  intègrent les nouveaux champs et `user_id` nul.
- `tests/api/test_teams_mandate_api.py` — la représentation publique inclut les
  Champs_Affichage ; ajout du cas `/equipes` public avec membres fondateurs.
- Nouveaux tests de rendu du gabarit et de migration (répertoires `tests/unit`
  et `tests/api` selon la portée).
