"""Vérifications de logique pure (Chunker, Deduplicator, TextCleaner)."""
import sys
sys.path.insert(0, ".")

from app.rag.ingestion.chunker import Chunker, ChunkMetadata
from app.rag.ingestion.dedup import Deduplicator
from app.rag.ingestion.cleaner import TextCleaner
from datetime import date

# --- Chunker invariants (Property 6) ---
ch = Chunker()
meta = ChunkMetadata(source_id=7, document_id=42, page=1, section="intro",
                     publication_date=date(2024, 1, 2))

for n_tokens in [0, 1, 50, 800, 801, 1000, 1560, 5000]:
    text = " ".join(f"w{i}" for i in range(n_tokens))
    chunks = ch.chunk(text, size_tokens=800, overlap_tokens=120, metadata=meta)
    if n_tokens == 0:
        assert chunks == [], "vide -> aucune sortie"
        continue
    # indices croissants a partir de 0
    assert [c.chunk_index for c in chunks] == list(range(len(chunks))), "indices"
    # tous sauf le dernier = 800 tokens
    for c in chunks[:-1]:
        assert c.token_count == 800, f"taille intermediaire {c.token_count}"
    assert chunks[-1].token_count <= 800
    # metadonnees completes sur chaque chunk
    for c in chunks:
        assert c.metadata.source_id == 7
        assert c.metadata.document_id == 42
        assert c.metadata.page == 1
        assert c.metadata.section == "intro"
        assert c.metadata.publication_date == date(2024, 1, 2)
    # chevauchement de 120 tokens entre chunks consecutifs
    if len(chunks) >= 2:
        for a, b in zip(chunks, chunks[1:]):
            ta = a.content.split()
            tb = b.content.split()
            assert ta[-120:] == tb[:120], "chevauchement 120"
    # token_count coherent avec le contenu
    for c in chunks:
        assert c.token_count == len(c.content.split())
    print(f"n_tokens={n_tokens}: {len(chunks)} chunks, tailles={[c.token_count for c in chunks]}")

# as_dict serialise la date
d = meta.as_dict()
assert d["publication_date"] == "2024-01-02", d
assert set(d) == {"source_id", "document_id", "page", "section", "publication_date"}

# param invalides
for bad in [dict(size_tokens=0), dict(overlap_tokens=-1), dict(overlap_tokens=800)]:
    try:
        ch.chunk("a b c", **bad)
        raise SystemExit(f"attendu ValueError pour {bad}")
    except ValueError:
        pass

# --- Deduplicator (Property 5 base) ---
d = Deduplicator()
c1 = d.checksum("hello world")
c2 = d.checksum("hello world")
assert c1 == c2 and len(c1) == 64, "checksum deterministe sha256"
assert d.checksum("other") != c1
assert d.is_known(c1) is False  # sans lookup
known = {c1}
d2 = Deduplicator(lookup=lambda cs: cs in known)
assert d2.is_known(c1) is True
assert d2.is_known(c2) is True  # meme contenu -> meme checksum -> connu
assert d2.is_known(d.checksum("brand new")) is False

# --- TextCleaner idempotence ---
tc = TextCleaner()
raw = "  Ligne 1 \r\n\r\n\r\nLigne\t\t2   avec   espaces  \r\n\n\n\n fin  "
once = tc.clean(raw)
twice = tc.clean(once)
assert once == twice, "clean idempotent"
assert "\r" not in once
assert "\n\n\n" not in once
assert "   " not in once
print("cleaned repr:", repr(once))

print("ALL_PURE_LOGIC_CHECKS_OK")
