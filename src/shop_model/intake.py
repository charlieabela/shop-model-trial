"""Taking in new data (Session 8, Stage 18): a supplier's batch, the ontology and the shapes.

    shop-intake try       answer the staff questions with a batch added (--batch is required)
    shop-intake infer     what a reasoner adds to the knowledge graph, given the ontology in kg/ontology.ttl
    shop-intake validate  check every resource against the shapes in kg/shapes.ttl

None of the three changes the store or the files in kg/. With --batch FILE each one works on the knowledge with
a supplier's batch added to it in memory.
"""
import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
from rdflib import RDF, RDFS, Graph
from rdflib.namespace import OWL, SH, SKOS

from shop_model.config import load_config
from shop_model.graph import SHOP, add_knowledge, ask
from shop_model.knowledge import check_against_schema, link_listings, load_knowledge, load_listings
from shop_model.logging_setup import setup_logging

ONTOLOGY_PATH = Path("kg/ontology.ttl")
SHAPES_PATH = Path("kg/shapes.ttl")


def load_batch(path):
    """A supplier's batch: one row per product, in the columns of kg/products.csv, with the product's category
    and the supplier's description."""
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def with_batch(kg, batch):
    """The knowledge with a batch added to it, in memory. The files in kg/ are not changed."""
    products = pd.concat([kg.products, batch[list(kg.products.columns)]], ignore_index=True)
    categories = pd.concat([kg.product_categories, batch[["product_id", "category"]]], ignore_index=True)
    return replace(kg, products=products, product_categories=categories)


def build(config, batch_path=None):
    """The knowledge graph built in memory from the files in kg/ and the listings, with a batch added if one is
    given. Nothing is written: the store and the files in kg/ stay as they are."""
    kg = load_knowledge()
    if batch_path:
        kg = with_batch(kg, load_batch(batch_path))
    listings = link_listings(load_listings(config), kg.products)
    g = Graph()
    add_knowledge(g, kg, listings)
    return g, kg


def try_batch(config, batch_path):
    """What Stage 17 can say about a batch: whether the knowledge still conforms to kg/schema.yaml, and whether
    the answers to the staff questions are still the expected ones."""
    g, kg = build(config, batch_path)
    violations = check_against_schema(kg)
    print(f"batch {batch_path}: {len(load_batch(batch_path))} products added in memory; "
          f"the knowledge graph has {len(g)} triples")
    print(f"kg/schema.yaml: {len(violations)} violations")
    for violation in violations:
        print(f"  {violation}")
    return ask(g)


def short(term):
    """A term as staff would read it: the last part of an IRI, or the text of a literal."""
    text = str(term)
    for prefix in (str(SHOP), str(SKOS), str(RDF), str(RDFS), str(OWL)):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def infer(config, batch_path=None):
    """What a reasoner adds to the knowledge graph, given the ontology. The reasoner applies the OWL 2 RL rules,
    which include those of RDFS. Only statements about the shop's own resources are counted."""
    from owlrl import DeductiveClosure, OWLRL_Semantics

    g, _ = build(config, batch_path)
    stated = set(g)
    ontology = Graph().parse(ONTOLOGY_PATH, format="turtle")
    declared = set(ontology.subjects())
    closed = Graph()
    for triple in list(g) + list(ontology):
        closed.add(triple)
    DeductiveClosure(OWLRL_Semantics).expand(closed)

    def about_the_shop(s, p, o):
        vocabulary = str(p).startswith(str(SHOP)) or (p == RDF.type and str(o).startswith((str(SHOP), str(SKOS))))
        return vocabulary and str(s).startswith(str(SHOP)) and s not in declared

    added = {t for t in closed if t not in stated and t not in ontology and about_the_shop(*t)}
    kinds = {}
    for _, p, o in added:
        kind = f"is a {short(o)}" if p == RDF.type else short(p)
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"stated in the knowledge graph about the shop's resources: {sum(about_the_shop(*t) for t in stated)}")
    print(f"added by the reasoner: {len(added)}")
    for kind, n in sorted(kinds.items()):
        print(f"  {kind:<20} {n:>5}")
    print("A reasoner adds statements. It does not report an error.")
    return 0


def validate(config, batch_path=None):
    """Check every resource against the shapes. Returns 0 when the knowledge graph conforms, 1 when it does not."""
    from pyshacl import validate as shacl_validate

    g, _ = build(config, batch_path)
    shapes = Graph().parse(SHAPES_PATH, format="turtle")
    conforms, report, _ = shacl_validate(g, shacl_graph=shapes, inference="none")
    results = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        focus = report.value(result, SH.focusNode)
        key = g.value(focus, SHOP.productId)
        path = report.value(result, SH.resultPath)
        message = report.value(result, SH.resultMessage)
        results.append((str(key) if key is not None else short(focus), short(path) if path else "", str(message)))
    what = f"kg/ with {batch_path}" if batch_path else "kg/"
    print(f"{what}: {len(g)} triples checked against {SHAPES_PATH}")
    print(f"conforms: {'yes' if conforms else 'no'}; {len(results)} violations")
    for key, path, message in sorted(results):
        print(f"  {key:<32} {path:<12} {message}")
    return 0 if conforms else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Taking in new data: a batch, the ontology and the shapes.")
    parser.add_argument("command", choices=["try", "infer", "validate"])
    parser.add_argument("--batch", help="a supplier's batch, added to the knowledge in memory")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "WARNING"))
    config = load_config()
    if args.command == "try":
        if not args.batch:
            print("A batch is needed: shop-intake try --batch incoming/harbour-cookware.csv", file=sys.stderr)
            return 2
        return try_batch(config, args.batch)
    if args.command == "infer":
        return infer(config, args.batch)
    return validate(config, args.batch)


if __name__ == "__main__":
    sys.exit(main())
