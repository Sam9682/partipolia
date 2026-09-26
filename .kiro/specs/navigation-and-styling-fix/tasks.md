# Implementation Plan

- [x] 1. Write bug condition exploration tests (BEFORE implementing the fix)
  - **Property 1: Bug Condition** - Navigation incomplète et style uniquement CDN
  - **CRITICAL**: These tests MUST FAIL on the unfixed code — failure confirms both bugs exist
  - **DO NOT attempt to fix the tests or the code when they fail** — a failure here is the expected outcome
  - **NOTE**: These tests encode the expected behavior; they will validate the fix once they pass after implementation
  - **GOAL**: Surface concrete counterexamples demonstrating each defect and confirm the root-cause analysis in design.md
  - **Scoped PBT Approach**: Both bug conditions are deterministic for a given SSR path. Scope the properties to concrete resolvable SSR paths (starting with `/programme`) and to the local stylesheet asset path.
  - Encode `isNavBugCondition(X)`: any page rendered via `base.html` (from Bug Condition in design.md). Render `GET /programme` with FastAPI/Starlette `TestClient`, parse the header `<nav>`, and assert the number of navigation links is `> 1` (Expected Behavior / Property 1). On unfixed code the header renders a single hard-coded `/programme` link, so the assertion FAILS.
  - Encode `isStylingBugCondition(X)`: page via `base.html` with no local stylesheet (from Bug Condition in design.md). Assert `GET /static/css/app.css` returns `200`, and assert the rendered `<head>` of `base.html` contains a local `<link rel="stylesheet" href="/static/css/app.css">`. On unfixed code the asset returns `404` and the `<head>` has no local `<link>`, so the assertions FAIL.
  - Add a scoped edge-case check documenting the dead-link risk: assert that no SSR route currently resolves for non-`/programme`/non-RGPD functions (justifies adding minimal index pages rather than raw links).
  - Run the tests on the UNFIXED code
  - **EXPECTED OUTCOME**: Tests FAIL (this is correct — it proves the bugs exist)
  - Document the counterexamples found (e.g., "header `<nav>` on `/programme` contains only 1 link"; "`GET /static/css/app.css` → 404"; "`<head>` of base.html has no local `<link rel=stylesheet>`")
  - Keep these tests scoped to the SSR/presentation layer; do NOT touch `app/api/v1/*`
  - Mark this task complete when the tests are written, run, and their failure is documented
  - _Requirements: 1.1, 1.2, 1.3 (targets Expected Behavior 2.1, 2.2, 2.3)_

- [x] 2. Write preservation property tests (BEFORE implementing the fix)
  - **Property 2: Preservation** - Comportement inchangé hors conditions de bug
  - **IMPORTANT**: Follow the observation-first methodology — run the UNFIXED code first, record actual outputs, then assert those outputs
  - Encode `NOT isNavBugCondition(X) AND NOT isStylingBugCondition(X)` from the Preservation Requirements in design.md
  - Observe on UNFIXED code and capture as tests:
    - `GET /programme` renders the Programme content (thèmes, mesures, votes, agrégats financiers) without authentication (Exigence 3.1) — record status and key content markers.
    - `GET /mentions-legales`, `/confidentialite`, `/cookies`, `/conditions` render without authentication (Exigence 3.2) — record status/content.
    - The four footer links (Mentions légales, Confidentialité, Cookies, Conditions) point to their respective pages (Exigence 3.3) — assert the footer `href`s are unchanged.
    - When the Tailwind CDN is referenced, the `<head>` keeps the existing CDN loading (`cdn.tailwindcss.com`) intact (Exigence 3.4) — assert the CDN `<script>`/link is still present.
    - A representative sample of `/api/v1/*` endpoints returns identical responses (status/shape) (Exigence 3.5) — record baseline responses.
  - Write these as property-based tests where practical (generate over the set of public SSR paths and a sampled set of `/api/v1/*` endpoints) for stronger "for all non-buggy inputs" guarantees, as recommended in design.md
  - Run the tests on the UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms the baseline behavior that must be preserved)
  - Mark this task complete when the tests are written, run, and passing on the unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for incomplete header navigation and CDN-only styling (SSR/presentation layer only)

  - [x] 3.1 Add the server-side navigation source of truth and shared SSR context in `app/web/router.py`
    - Define the `nav_items` list (ordered `{label, href}` objects) as the single source of truth for the header menu
    - Include ONLY resolvable targets: `/programme` (existing), the new SSR index/list pages (Thèmes, Propositions, Statistiques, Équipes, Mandat), plus "Assistant" (chat) and "API" (OpenAPI docs `/docs`) so no entry is a dead link
    - Factor `nav_items` into a shared context helper (utility function or context-processor equivalent) applied to every SSR `TemplateResponse` to avoid duplication
    - _Bug_Condition: isNavBugCondition(X) where X.rendersLayout = base_html (from design.md)_
    - _Expected_Behavior: headerNav(result).count > 1 AND contains links_to_main_functions AND all links resolve (from design.md Property 1)_
    - _Requirements: 2.1_

  - [x] 3.2 Add minimal read-only public SSR list views in `app/web/router.py`
    - Add public, unauthenticated, read-only SSR views for Thèmes, Propositions, Statistiques, Équipes, and Mandat, backed by existing services (`ThemeService`, `ProposalService`/`ProgramService`, `StatisticsService`, `TeamService`, mandate)
    - Each view renders its `templates/{fonction}.html` and injects the shared `nav_items` context; keep scope limited to list pages sufficient to make the nav link resolvable and useful
    - Do NOT modify any file under `app/api/v1/*`
    - _Bug_Condition: isNavBugCondition(X) — links must resolve, so index pages replace raw dead links (from design.md)_
    - _Expected_Behavior: all header links resolve (no 404) (from design.md Property 1)_
    - _Requirements: 2.1_

  - [x] 3.3 Add the new SSR list templates under `app/templates/{fonction}.html`
    - Create list templates for Thèmes, Propositions, Statistiques, Équipes, Mandat, each extending `base.html` and reusing existing classes for consistency with `programme.html`
    - _Bug_Condition: isNavBugCondition(X) (from design.md)_
    - _Expected_Behavior: pages render via base.html and are reachable from the header (from design.md Property 1)_
    - _Requirements: 2.1_

  - [x] 3.4 Add the compiled local stylesheet `app/static/css/app.css` (new)
    - Produce a self-contained local CSS covering the layout and components used by `base.html` and the SSR templates (container, header/footer, cards, tables, buttons/links, typography)
    - Generate via a Tailwind CLI build restricted to the classes actually used by the templates, or provide an equivalent static CSS; commit the compiled asset so it is served with no runtime network dependency (served by the existing `StaticFiles` mount on `/static`)
    - _Bug_Condition: isStylingBugCondition(X) where tailwindCdnAvailable = false AND hasLocalStylesheet = false (from design.md)_
    - _Expected_Behavior: isStyled(result) = true AND servesLocalStylesheet('/static') = true (from design.md Property 1)_
    - _Requirements: 2.2, 2.3_

  - [x] 3.5 Update `app/templates/base.html` (data-driven nav + local stylesheet link)
    - Replace the single hard-coded `/programme` link with a Jinja2 loop over `nav_items`, with a safe fallback (at least the "Programme" link) when `nav_items` is absent so behavior never regresses below the current state
    - Add `<link rel="stylesheet" href="/static/css/app.css">` in the `<head>`, loaded in addition to the CDN so it provides correct rendering when the CDN is absent without overriding/breaking the Tailwind CDN when present
    - Keep the existing CDN `<script>`/link loading unchanged (Exigence 3.4)
    - _Bug_Condition: isNavBugCondition(X) AND isStylingBugCondition(X) (from design.md)_
    - _Expected_Behavior: headerNav count > 1 with resolvable links; local stylesheet linked and page styled (from design.md Property 1)_
    - _Preservation: existing CDN loading and footer links unchanged (from design.md Preservation Requirements)_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.6 Verify the bug condition exploration tests now pass
    - **Property 1: Expected Behavior** - Navigation complète et style local fiable
    - **IMPORTANT**: Re-run the SAME tests from task 1 — do NOT write new tests
    - The tests from task 1 encode the expected behavior; when they pass, they confirm the expected behavior is satisfied
    - Run the bug condition exploration tests from task 1
    - **EXPECTED OUTCOME**: Tests PASS — header `<nav>` on SSR pages has `count > 1` with only resolvable links, `GET /static/css/app.css` returns `200`, and `base.html` `<head>` contains the local `<link rel="stylesheet">`
    - _Requirements: 2.1, 2.2, 2.3 (Expected Behavior / Property 1 from design.md)_

  - [x] 3.7 Verify the preservation tests still pass
    - **Property 2: Preservation** - Comportement inchangé hors conditions de bug
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run the preservation property tests from task 2
    - **EXPECTED OUTCOME**: Tests PASS — `/programme` and RGPD pages still render without authentication, footer links unchanged, CDN loading intact when present, and sampled `/api/v1/*` responses identical (no regressions)
    - Confirm no file under `app/api/v1/*` was modified
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full relevant test suite: exploration tests (task 1), preservation tests (task 2), and any unit/integration tests for the new SSR views and static asset
  - Fix-Checking mapping: header nav count `> 1` with no dead links (every header link resolves to a `200`), local stylesheet served under `/static`, and pages styled
  - Preservation mapping: `/programme`, RGPD pages, footer links, CDN-present rendering, and `/api/v1/*` all unchanged
  - Confirm the changes remain scoped to the SSR/presentation layer only
  - Ensure all tests pass; ask the user if questions arise
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5_
