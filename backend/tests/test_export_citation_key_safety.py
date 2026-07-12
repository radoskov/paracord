"""Citation-key safety for exports (F1).

A user-supplied ``citation_keys`` override is emitted into structural positions in BibTeX / LaTeX /
Pandoc / CSL-JSON. These tests prove two things:

1. **Injection is neutralised** — a crafted key cannot break out of the entry (no ``{ } , \\`` /
   whitespace survive in the emitted key).
2. **Legitimate content is preserved** — Unicode letters and the punctuation real keys use
   (``. : + / _ -``) are kept, and *field values* (accented author names, DOIs, years) pass through
   completely untouched (they are escaped, never stripped).
"""

import json
import re

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.export_service import export_bibliography

_INJECTION = "evil},title={PWNED},x={"
_KEY_OK = re.compile(r"^[\w.:+/-]+$", re.UNICODE)


def _seed_work(db, *, title, doi, authors, year=2017):
    work = Work(canonical_title=title, normalized_title=title.lower(), year=year, doi=doi)
    db.add(work)
    db.flush()
    db.add(
        MetadataAssertion(
            entity_type="work",
            entity_id=work.id,
            field_name="authors",
            value=authors,
            source="arxiv",
            confidence=0.9,
            selected_as_canonical=True,
        )
    )
    db.commit()
    return work


def _export(db, work, fmt, override):
    return export_bibliography(
        db,
        scope_type="selection",
        output_format=fmt,
        work_ids=[str(work.id)],
        citation_keys={str(work.id): override},
    )


def _bibtex_key(content: str) -> str:
    m = re.search(r"@\w+\{([^,\n]+),", content)
    assert m, f"no bibtex entry key found in:\n{content}"
    return m.group(1)


def test_injection_payload_is_neutralised_bibtex(db):
    work = _seed_work(db, title="Attention", doi="10.5555/x", authors="A. Bee")
    out = _export(db, work, "bibtex", _INJECTION)
    key = _bibtex_key(out)
    assert _KEY_OK.match(key), key
    for bad in "{},\\ \t\n":
        assert bad not in key
    # Exactly one entry / one opening brace pair — the payload did not inject a second field.
    assert out.count("@article{") == 1
    assert "title={PWNED}" not in out.replace(" ", "")


def test_injection_payload_is_neutralised_latex_and_pandoc(db):
    work = _seed_work(db, title="Attention", doi="10.5555/x", authors="A. Bee")

    latex = _export(db, work, "latex", _INJECTION)
    for m in re.findall(r"\\bibitem\{([^}]*)\}", latex):
        assert _KEY_OK.match(m), m
    cite = re.search(r"\\cite\{([^}]*)\}", latex).group(1)
    assert all(_KEY_OK.match(k) for k in cite.split(","))

    pandoc = _export(db, work, "pandoc", _INJECTION)
    for m in re.findall(r"@([\w.:+/-]+)", pandoc):
        assert _KEY_OK.match(m)
    # The '{' from the payload must not survive anywhere in the pandoc citation output.
    assert "{" not in pandoc


def test_injection_payload_is_neutralised_csl_json(db):
    work = _seed_work(db, title="Attention", doi="10.5555/x", authors="A. Bee")
    out = _export(db, work, "csl-json", _INJECTION)
    items = json.loads(out)  # must be valid JSON
    assert len(items) == 1
    assert _KEY_OK.match(items[0]["id"]), items[0]["id"]


def test_legitimate_key_characters_are_preserved(db):
    """Unicode + DBLP/DOI-style punctuation must survive verbatim, not be stripped."""
    work = _seed_work(db, title="Study", doi="10.1/x", authors="A. Bee")
    for good in ("Müller:2020/study.v2", "DBLP:journals/corr/abs-1234", "smith_2020+ext"):
        out = _export(db, work, "bibtex", good)
        assert _bibtex_key(out) == good, f"legit key mangled: {good!r} -> {_bibtex_key(out)!r}"


def test_field_values_pass_through_untouched(db):
    """Accented author names, DOIs, and years live in field values and must never be stripped."""
    work = _seed_work(
        db,
        title="Modèles de séquences",
        doi="10.1000/j.cell.2020.01.001",
        authors="José Peña; Łukasz Kaiser",
        year=2021,
    )
    out = _export(db, work, "bibtex", "evil},x={")
    assert "José Peña" in out
    assert "Łukasz Kaiser" in out
    assert "10.1000/j.cell.2020.01.001" in out
    assert "2021" in out
    assert "Modèles de séquences" in out


def test_colliding_overrides_are_deduplicated(db):
    w1 = _seed_work(db, title="One", doi="10.1/a", authors="A. Bee")
    w2 = _seed_work(db, title="Two", doi="10.1/b", authors="C. Dee")
    out = export_bibliography(
        db,
        scope_type="selection",
        output_format="bibtex",
        work_ids=[str(w1.id), str(w2.id)],
        citation_keys={str(w1.id): "same2020", str(w2.id): "same2020"},
    )
    keys = re.findall(r"@\w+\{([^,\n]+),", out)
    assert len(keys) == 2
    assert len(set(keys)) == 2, f"duplicate citation keys not de-duplicated: {keys}"
    assert all(_KEY_OK.match(k) for k in keys)
