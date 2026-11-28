"""The ontology, the shapes and the supplier's batch (Session 8, Stage 18)."""
from pathlib import Path

import pytest
from rdflib import OWL, RDF, Graph

from shop_model.config import load_config
from shop_model.graph import SHOP
from shop_model.intake import ONTOLOGY_PATH, SHAPES_PATH, build, load_batch, validate, with_batch
from shop_model.knowledge import check_against_schema, load_knowledge, load_schema

BATCH = Path("incoming/harbour-cookware.csv")
PROPERTY_OF = {"IN_CATEGORY": "inCategory", "IN_RANGE": "inRange", "MADE_OF": "madeOf", "CONTAINS": "contains",
               "GOES_WITH": "goesWith", "OF": "of"}


@pytest.fixture(scope="module")
def config():
    return load_config()


def test_the_ontology_declares_every_class_of_the_schema():
    ontology = Graph().parse(ONTOLOGY_PATH, format="turtle")
    declared = {str(c).removeprefix(str(SHOP)) for c in ontology.subjects(RDF.type, OWL.Class)}
    assert set(load_schema()["classes"]) - {"Term"} <= declared   # a term is a label, not a resource


def test_the_ontology_declares_every_relationship_of_the_schema():
    ontology = Graph().parse(ONTOLOGY_PATH, format="turtle")
    declared = {str(p).removeprefix(str(SHOP)) for p in ontology.subjects(RDF.type, OWL.ObjectProperty)}
    names = {spec["name"] for spec in load_schema()["relationships"]} - {"BROADER", "MEANS"}   # these two are SKOS
    assert {PROPERTY_OF[name] for name in names} <= declared


def test_the_shapes_are_valid_turtle_and_target_the_shop_classes():
    from rdflib.namespace import SH
    shapes = Graph().parse(SHAPES_PATH, format="turtle")
    targets = {str(c).removeprefix(str(SHOP)) for c in shapes.objects(None, SH.targetClass)}
    assert targets == {"Product", "GiftSet", "Category", "Material", "Range", "Listing", "Supplier", "Batch"}


def test_the_released_knowledge_conforms_to_the_shapes(config):
    assert validate(config) == 0


def test_the_batch_conforms_to_the_schema_of_stage_16():
    # From Stage 19 most of the batch is in kg/ already, so only the rows not yet accepted are added here.
    kg = load_knowledge()
    batch = load_batch(BATCH)
    kg = with_batch(kg, batch[~batch["product_id"].isin(kg.products["product_id"])])
    assert check_against_schema(kg) == []


def test_the_batch_does_not_conform_to_the_shapes(config, capsys):
    assert validate(config, BATCH) == 1
    out = capsys.readouterr().out
    assert "2 violations" in out
    assert "HC_0518" in out and "copper-jam-pan" in out


def test_trying_a_batch_changes_no_file(config):
    before = Path("kg/products.csv").read_bytes()
    g, kg = build(config, BATCH)
    assert len(kg.products) == len(before.decode().strip().splitlines()) - 1 + len(load_batch(BATCH))
    assert Path("kg/products.csv").read_bytes() == before
