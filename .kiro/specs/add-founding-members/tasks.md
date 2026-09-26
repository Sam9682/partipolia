# Implementation Plan: add-founding-members

## Overview

Extend `TeamMember` with display fields and a nullable `user_id`, ship two Alembic
migrations (schema change `0003`, founding-members seed `0004`), update the Pydantic
schemas, render name/photo/initials/LinkedIn in `equipes.html`, add Samuel's static
photo, and cover everything with property, example, and integration/migration tests.
Each step builds on the previous one and ends wired into the running SSR page and API.

## Tasks

- [x] 1. Extend the `TeamMember` ORM model
  - [x] 1.1 Add display fields and make `user_id` nullable in `app/models/team.py`
    - Add `display_name` (`String(255)`, nullable), `photo_path` (`String(512)`, nullable), `linkedin_url` (`String(512)`, nullable)
    - Change `user_id` to `Mapped[int | None]` with `nullable=True`, keeping the FK to `users`
    - Make the `user` relationship optional (`Mapped["User | None"]`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.2 Write example/unit tests for the extended model
    - Introspect `TeamMember`: `display_name` is `String(255)`, `photo_path`/`linkedin_url` nullable, `user_id` nullable, FK to `users` preserved
    - Instantiate a member with `user_id=None`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. Schema migration `0003_team_member_display_fields`
  - [x] 2.1 Create `alembic/versions/0003_team_member_display_fields.py`
    - `down_revision = "0002_user_consents"`
    - `upgrade`: add `display_name`, `photo_path`, `linkedin_url`; `alter_column` `user_id` to nullable
    - `downgrade`: restore `user_id` NOT NULL, then drop the three display columns
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 3. Founding-members seed migration `0004_seed_founding_members`
  - [x] 3.1 Create `alembic/versions/0004_seed_founding_members.py`
    - `down_revision = "0003_team_member_display_fields"`
    - Shared constants for team name, photo path, LinkedIn URL, member names
    - `upgrade`: insert team « Fondateurs »; insert Samuel Lepetre (photo `img/team/samuel-lepetre.jpg` + LinkedIn URL) and Nael Lepetre (NULL photo/LinkedIn), both with `user_id = NULL`
    - `downgrade`: delete both members by `display_name` scoped to the « Fondateurs » team, then delete the team
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Update the public Pydantic schemas
  - [x] 5.1 Extend `TeamMemberPublic` and `TeamMemberCreate` in `app/schemas/team.py`
    - `TeamMemberPublic`: add `user_id: int | None`, `display_name`, `photo_path`, `linkedin_url` (all optional)
    - `TeamMemberCreate`: add `display_name` (≤255), `photo_path` (≤512), `linkedin_url` (≤512), make `user_id` optional
    - _Requirements: 5.1, 5.2_

  - [ ]* 5.2 Write property test for public representation round-trip
    - **Feature: add-founding-members, Property 5: Aller-retour des Champs_Affichage dans la représentation publique**
    - Generate `TeamMember`-like objects (including `user_id=None`); assert `TeamMemberPublic.model_validate(m).model_dump()` restores the four display fields and accepts null `user_id`
    - **Validates: Requirements 5.1, 5.2**

- [x] 6. Add Samuel's static photo
  - [x] 6.1 Create `app/static/img/team/` and place `samuel-lepetre.jpg`
    - Ensure the file exists at `app/static/img/team/samuel-lepetre.jpg` matching the seeded `photo_path`
    - _Requirements: 4.1_

- [x] 7. Update the `equipes.html` SSR template
  - [x] 7.1 Add the `initials` macro and rich member rendering in `app/templates/equipes.html`
    - Add Jinja macro `initials(name)` (first letter of the first two words, uppercased)
    - Render `display_name` when present, else fall back to `Utilisateur #user_id`
    - When `photo_path` set, render `<img src="{{ url_for('static', path=member.photo_path) }}" alt="{{ display_name }}">`, else render the initials `<span>`
    - When `linkedin_url` set, render `<a href="…" rel="noopener" target="_blank">`, else no link
    - Preserve existing `data-testid` (`team`, `team-member`) and accessibility attributes
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 7.2 Write property test: display name replaces user identifier
    - **Feature: add-founding-members, Property 1: Le nom affiché remplace l'identifiant Utilisateur**
    - Generate members with varied `display_name`; render the row/template; assert name present and no « Utilisateur #… » for that member (≥100 iterations)
    - **Validates: Requirements 2.1**

  - [ ]* 7.3 Write property test: photo or initials substitute, never both
    - **Feature: add-founding-members, Property 2: Photo ou substitut, jamais les deux**
    - Generate members with/without `photo_path`; assert `<img>` referencing the path when set, else initials substitute and no image (≥100 iterations)
    - **Validates: Requirements 2.2, 2.3**

  - [ ]* 7.4 Write property test: deterministic initials derivation
    - **Feature: add-founding-members, Property 3: Dérivation déterministe des initiales**
    - Generate names (multi-word, accents, single word, empty); assert deterministic uppercase initials of the first two words (≥100 iterations)
    - **Validates: Requirements 2.3**

  - [ ]* 7.5 Write property test: conditional LinkedIn link presence
    - **Feature: add-founding-members, Property 4: Présence conditionnelle du lien LinkedIn**
    - Generate members with/without `linkedin_url`; assert a link with href exactly equal to `linkedin_url` iff it is non-null (≥100 iterations)
    - **Validates: Requirements 2.4, 2.5**

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Integration and wiring
  - [x] 9.1 Verify `TeamService.list_teams()` returns founders with display fields
    - Confirm the « Fondateurs » team and both members (with display fields, null `user_id`) are returned via `selectin` loading without changes to `list_teams`
    - Update `tests/unit/test_team_service.py` `_FakeSession` and assertions to include the new fields
    - _Requirements: 5.3_

  - [ ]* 9.2 Write public route example test for `/equipes`
    - `GET /equipes` without authentication returns 200 and renders the members list including founders
    - _Requirements: 2.6, 5.3_

  - [ ]* 9.3 Write migration integration tests
    - Apply migrations then assert: team « Fondateurs » created; Samuel (photo + LinkedIn), Nael (nulls), null `user_id` for both; after `downgrade`, team and both members are gone
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 9.4 Write static photo serving tests
    - Smoke: photo exists under `app/static/img/team/` and seeded `photo_path` points into it
    - `GET` of the static photo URL returns 200 with an image content type
    - _Requirements: 4.1, 4.2_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP.
- Each task references specific requirements for traceability.
- Checkpoints ensure incremental validation.
- Property tests validate universal correctness properties (≥100 iterations) and reference the design's Correctness Properties by number.
- Unit/example and integration/migration tests validate structural facts and concrete scenarios.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "6.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "5.1", "7.1"] },
    { "id": 2, "tasks": ["3.1", "5.2", "7.2", "7.3", "7.4", "7.5"] },
    { "id": 3, "tasks": ["9.1", "9.4"] },
    { "id": 4, "tasks": ["9.2", "9.3"] }
  ]
}
```
