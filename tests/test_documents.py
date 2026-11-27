"""The document layer (Session 9): the documents in corpus/ and their links in kg/documents.csv."""
from pathlib import Path

from shop_model.knowledge import check_against_schema, documents_for, load_knowledge

CORPUS = Path("corpus")


def test_knowledge_with_documents_conforms_to_schema():
    assert check_against_schema(load_knowledge()) == []


def test_every_linked_document_has_a_file_and_every_file_is_linked():
    linked = set(load_knowledge().documents["doc_id"])
    files = {path.stem for path in CORPUS.glob("*.md")}
    assert linked == files


def test_documents_for_the_copper_pan():
    docs = documents_for("copper-saute-pan-24", load_knowledge())
    assert {"care-cp24", "spec-cp24", "warranty", "returns-premium", "guide-cookware"} <= docs
    assert "care-cp26" not in docs
    assert "returns-standard" not in docs


def test_documents_for_the_stainless_pan():
    docs = documents_for("stainless-saute-pan-26", load_knowledge())
    assert {"care-cp26", "spec-cp26", "warranty", "returns-standard", "guide-cookware"} <= docs
    assert "care-cp24" not in docs
    assert "returns-premium" not in docs


def test_a_product_with_no_document_of_its_own_still_gets_the_policies_of_its_range():
    docs = documents_for("cast-iron-casserole", load_knowledge())
    assert docs == {"returns-premium", "warranty"}
