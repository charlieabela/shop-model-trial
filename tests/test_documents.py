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


def test_a_document_about_a_material_reaches_products_of_a_narrower_material():
    kg = load_knowledge()
    assert "care-wood" in documents_for("olive-wood-salad-servers", kg)
    assert "care-wood" in documents_for("oak-bookends", kg)


def test_bamboo_is_not_wood_for_the_care_notes_either():
    docs = documents_for("bamboo-coaster-set", load_knowledge())
    assert "care-plant-fibre" in docs
    assert "care-wood" not in docs


def test_a_finer_wool_receives_both_care_notes():
    docs = documents_for("cashmere-throw-charcoal", load_knowledge())
    assert {"care-cashmere", "care-wool", "guide-throws", "returns-premium"} <= docs
    assert "care-cashmere" not in documents_for("wool-throw-blanket-grey", load_knowledge())


def test_a_document_about_a_department_reaches_products_two_levels_down():
    assert "guide-garden" in documents_for("copper-watering-can", load_knowledge())


def test_a_gift_set_receives_the_documents_of_its_contents_and_the_policies_of_its_own_range():
    docs = documents_for("gift-set-serving", load_knowledge())
    assert {"care-wood", "guide-serveware", "policy-gift-sets", "returns-premium"} <= docs
    assert "returns-standard" not in docs   # the olive wood servers are core range, the set is premium


def test_no_document_names_a_product_it_is_not_directly_about():
    kg = load_knowledge()
    direct = set(kg.products["product_id"])
    for doc_id, about in kg.documents[["doc_id", "about"]].itertuples(index=False):
        if about in direct:
            continue
        text = (CORPUS / f"{doc_id}.md").read_text(encoding="utf-8").lower()
        for name in kg.products["name"]:
            assert name.lower() not in text, (doc_id, name)


def test_the_graph_finds_the_same_documents_as_the_files():
    from rdflib import Graph

    from shop_model.config import load_config
    from shop_model.graph import add_knowledge
    from shop_model.graph import documents_for as documents_in_graph
    from shop_model.knowledge import link_listings, load_listings

    kg = load_knowledge()
    listings = link_listings(load_listings(load_config()), kg.products)
    g = add_knowledge(Graph(), kg, listings)
    for product_id in kg.products["product_id"]:
        assert documents_in_graph(g, product_id) == documents_for(product_id, kg), product_id
