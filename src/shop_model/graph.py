"""The shop's knowledge layer as an RDF graph (Session 7, Stage 17; Session 8, Stage 18).

    shop-kg load    add the knowledge files and the listings to the graph store, kg/build/shop.nt
    shop-kg count   triples in the store, instances of each class, uses of each property, and the store's SHA-256
    shop-kg ask     answer the staff questions in SPARQL and score them, as shop-ask does
    shop-kg check   what make ci runs: build the store from nothing, load it again, require the same SHA-256, and
                    answer every question from the store as written

Session 8, Stage 18. None of these three changes the store or the files in kg/. With --batch FILE each one works
on the knowledge with a supplier's batch added to it in memory.

    shop-kg try       answer the staff questions with a batch added (--batch is required)
    shop-kg infer     what a reasoner adds, given the ontology in kg/ontology.ttl
    shop-kg validate  check every resource against the shapes in kg/shapes.ttl

The store is a file of N-Triples, one triple per line, written in sorted order: the same content always gives
the same file, and so the same SHA-256. Loading adds to what the store already holds, as loading into any triple
store does. It is idempotent because every resource is named by an IRI minted from its key, and a graph is a set
of triples: adding a triple the graph already holds changes nothing.
"""
import argparse
import hashlib
import logging
import os
import sys
from decimal import Decimal
from pathlib import Path

import yaml
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace
from rdflib.namespace import OWL, SKOS

from shop_model.ask import fmt, score
from shop_model.config import load_config
from shop_model.knowledge import (
    QUESTIONS_PATH,
    check_against_schema,
    link_listings,
    load_batch,
    load_knowledge,
    load_listings,
    load_questions,
    load_schema,
    with_batch,
)
from shop_model.logging_setup import setup_logging

logger = logging.getLogger(__name__)

STORE_PATH = Path("kg/build/shop.nt")
ONTOLOGY_PATH = Path("kg/ontology.ttl")
SHAPES_PATH = Path("kg/shapes.ttl")

# Every IRI the shop mints starts here. example.org is reserved for examples, so it can never clash with a real site.
SHOP = Namespace("https://ari3217.example.org/shop/")
TAXONOMY = SHOP["scheme/taxonomy"]
MATERIALS = SHOP["scheme/materials"]
# The graph says which release of the knowledge it was built from, so any answer can be traced to a release.
RELEASE = SHOP["knowledge"]


# One function per kind of resource: the IRI is the key, so the same thing always gets the same name.
def product_iri(product_id):
    return SHOP[f"product/{product_id}"]


def concept_iri(concept_id):
    return SHOP[f"category/{concept_id}"]


def material_iri(material_id):
    return SHOP[f"material/{material_id}"]


def range_iri(range_id):
    return SHOP[f"range/{range_id}"]


def listing_id(row):
    """A listing is one product, put on sale on one day, at one price. The same product is sometimes listed twice
    on the same day, so the date and the product alone are not enough."""
    return f"{row['listed_at']}|{row['product_id']}|{row['price']:.2f}"


def listing_iri(row):
    return SHOP[f"listing/{row['listed_at']}/{row['product_id']}/{row['price']:.2f}"]


def load_relationship_questions(path=QUESTIONS_PATH):
    with open(path) as f:
        return yaml.safe_load(f).get("relationship_questions", [])


def add_knowledge(g, kg, listings, version=None):
    """Add the knowledge files and the listings to the graph g, as triples."""
    g.bind("shop", SHOP)
    g.bind("skos", SKOS)
    g.bind("owl", OWL)
    g.add((RELEASE, RDF.type, SHOP.KnowledgeRelease))
    g.add((RELEASE, OWL.versionInfo, Literal(version or load_schema()["version"])))
    # The taxonomy and the materials are two SKOS concept schemes. skos:broader says "is a kind of".
    for scheme, label in ((TAXONOMY, "The shop's taxonomy"), (MATERIALS, "The shop's materials")):
        g.add((scheme, RDF.type, SKOS.ConceptScheme))
        g.add((scheme, SKOS.prefLabel, Literal(label, lang="en")))
    for cid, c in kg.concepts.items():
        node = concept_iri(cid)
        g.add((node, RDF.type, SKOS.Concept))
        g.add((node, RDF.type, SHOP.Category if c.get("is_a") else SHOP.Department))
        g.add((node, SKOS.prefLabel, Literal(c["name"], lang="en")))
        g.add((node, SKOS.inScheme, TAXONOMY))
        if c.get("is_a"):
            g.add((node, SKOS.broader, concept_iri(c["is_a"])))
        else:
            g.add((node, SKOS.topConceptOf, TAXONOMY))
    for mid, m in kg.materials.items():
        node = material_iri(mid)
        g.add((node, RDF.type, SKOS.Concept))
        g.add((node, RDF.type, SHOP.Material))
        g.add((node, SKOS.prefLabel, Literal(m["name"], lang="en")))
        g.add((node, SKOS.inScheme, MATERIALS))
        if m.get("is_a"):
            g.add((node, SKOS.broader, material_iri(m["is_a"])))
    for rid, r in kg.ranges.items():
        node = range_iri(rid)
        g.add((node, RDF.type, SHOP.Range))
        g.add((node, RDFS.label, Literal(r["name"], lang="en")))
        if r.get("launched"):
            g.add((node, SHOP.launched, Literal(r["launched"], datatype=XSD.date)))
    # The staff vocabulary: each word is an alternative label of the concept, range or material it means.
    for term, target in kg.synonyms.items():
        if target in kg.ranges:
            node = range_iri(target)
        elif target in kg.materials:
            node = material_iri(target)
        else:
            node = concept_iri(target)
        g.add((node, SKOS.altLabel, Literal(term, lang="en")))
    for p in kg.products.to_dict("records"):
        node = product_iri(p["product_id"])
        g.add((node, RDF.type, SHOP.Product))
        if p["class"] == "GiftSet":  # a gift set is a kind of product
            g.add((node, RDF.type, SHOP.GiftSet))
        g.add((node, SHOP.productId, Literal(p["product_id"])))
        g.add((node, RDFS.label, Literal(p["name"], lang="en")))
        g.add((node, SHOP.inRange, range_iri(p["range"])))
        if p["material"]:  # an empty material is not known: no triple is written, rather than a guess
            g.add((node, SHOP.madeOf, material_iri(p["material"])))
    for m in kg.product_categories.itertuples():
        g.add((product_iri(m.product_id), SHOP.inCategory, concept_iri(m.category)))
    for item in kg.gift_sets.itertuples():
        g.add((product_iri(item.gift_set_id), SHOP.contains, product_iri(item.product_id)))
    # Complements are stated between categories, once, in one direction and meant both ways. The queries derive the
    # pairs of products, following the rule with the path shop:goesWith|^shop:goesWith.
    for rule in kg.complements:
        first, second = rule["categories"]
        g.add((concept_iri(first), SHOP.goesWith, concept_iri(second)))
    for row in listings.to_dict("records"):
        node = listing_iri(row)
        g.add((node, RDF.type, SHOP.Listing))
        g.add((node, SHOP.of, product_iri(row["product_id"])))
        g.add((node, SHOP.listedAt, Literal(row["listed_at"], datatype=XSD.date)))
        g.add((node, SHOP.price, Literal(Decimal(f"{row['price']:.2f}"), datatype=XSD.decimal)))
        g.add((node, SHOP.sold, Literal(bool(row["sold"]), datatype=XSD.boolean)))
    return g


def open_store(path=STORE_PATH):
    g = Graph()
    g.bind("shop", SHOP)
    g.bind("skos", SKOS)
    if path.exists():
        g.parse(path, format="nt")
    return g


def save_store(g, path=STORE_PATH):
    """Write the graph as sorted N-Triples, so that the same graph always gives the same file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = sorted(line for line in g.serialize(format="nt").splitlines() if line.strip())
    path.write_text("\n".join(lines) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(config):
    kg = load_knowledge()
    listings = link_listings(load_listings(config), kg.products)
    g = open_store()
    before = len(g)
    add_knowledge(g, kg, listings)
    fingerprint = save_store(g)
    logger.info("Store %s: %d triples before, %d after", STORE_PATH, before, len(g))
    print(f"triples before {before}, after {len(g)}; sha256 {fingerprint[:16]}")


PREFIXES = """
PREFIX shop: <https://ari3217.example.org/shop/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""

COUNT_CLASSES = PREFIXES + """
SELECT ?class (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a ?class }
GROUP BY ?class ORDER BY ?class
"""

COUNT_PROPERTIES = PREFIXES + """
SELECT ?property (COUNT(*) AS ?n) WHERE { ?s ?property ?o }
GROUP BY ?property ORDER BY ?property
"""


def count(g):
    print(f"{'triples':<24} {len(g):>6}")
    for row in g.query(COUNT_CLASSES):
        print(f"{g.qname(row['class']):<24} {int(row['n']):>6}")
    for row in g.query(COUNT_PROPERTIES):
        print(f"{g.qname(row['property']):<24} {int(row['n']):>6}")
    for version in sorted(str(v) for v in g.objects(RELEASE, OWL.versionInfo)):
        print(f"{'release':<24} {version:>6}")
    if STORE_PATH.exists():
        print(f"{'sha256':<24} {hashlib.sha256(STORE_PATH.read_bytes()).hexdigest()[:16]}")


# One query answers every staff question about listings: from the question's words, through the vocabulary
# (skos:altLabel), to a concept; from the concept to its products, through a range, or down either hierarchy to any
# depth; and on to the listings that meet the conditions. skos:broader* follows zero or more steps: zero for the
# concept itself.
COVERS = """
  VALUES ?term { %(terms)s }
  ?concept skos:altLabel ?term .
  { ?product shop:inRange ?concept }
  UNION { ?product shop:inCategory/skos:broader* ?concept }
  UNION { ?product shop:madeOf/skos:broader* ?concept }
"""

ANSWER = PREFIXES + """
SELECT DISTINCT ?id WHERE {
""" + COVERS + """
  ?listing shop:of ?product ; shop:price ?price ; shop:sold ?sold .
  ?product shop:productId ?id .
  %(conditions)s
}
ORDER BY ?id
"""

# The same words, to the products in the catalogue that no listing points to.
NEVER_LISTED = PREFIXES + """
SELECT DISTINCT ?id WHERE {
""" + COVERS + """
  FILTER NOT EXISTS { ?listing shop:of ?product }
  ?product shop:productId ?id .
}
ORDER BY ?id
"""

# Gift sets that contain at least one product the words cover.
CONTAINS = PREFIXES + """
SELECT DISTINCT ?id WHERE {
""" + COVERS + """
  ?set a shop:GiftSet ; shop:contains ?product ; shop:productId ?id .
}
ORDER BY ?id
"""

# Which products in one range go with a product in another range that has sold. No pair of products is stored:
# each pair is derived from a rule between two categories, through the categories each product is in, at any depth.
CROSS_SELL = PREFIXES + """
SELECT DISTINCT ?id WHERE {
  ?base shop:inRange ?from .
  FILTER EXISTS { ?listing shop:of ?base ; shop:sold true }
  ?base shop:inCategory/skos:broader* ?category .
  ?category shop:goesWith|^shop:goesWith ?other_category .
  ?other shop:inCategory/skos:broader* ?other_category ; shop:inRange ?to ; shop:productId ?id .
  FILTER (?other != ?base)
}
ORDER BY ?id
"""


def terms_of(question):
    return " ".join(Literal(t, lang="en").n3() for t in question["terms"])


def answer(g, question):
    if question.get("listed") is False:
        return {str(row["id"]) for row in g.query(NEVER_LISTED % {"terms": terms_of(question)})}
    terms = terms_of(question)
    conditions = []
    if question.get("max_price") is not None:
        conditions.append(f"FILTER (?price < {Decimal(str(question['max_price']))})")
    if question.get("sold"):
        conditions.append("FILTER (?sold = true)")
    query = ANSWER % {"terms": terms, "conditions": "\n  ".join(conditions)}
    return {str(row["id"]) for row in g.query(query)}


def contains(g, question):
    return {str(row["id"]) for row in g.query(CONTAINS % {"terms": terms_of(question)})}


def cross_sell(g, question):
    bindings = {"from": range_iri(question["from_range"]), "to": range_iri(question["to_range"])}
    return {str(row["id"]) for row in g.query(CROSS_SELL, initBindings=bindings)}


def ask(g):
    print(f"{'question':<12} {'route':<10} {'found':>5} {'right':>5} {'expected':>8} {'precision':>9} {'recall':>6}")
    failures = 0
    rows = [(q, answer(g, q)) for q in load_questions()]
    rows += [(q, cross_sell(g, q) if q["kind"] == "cross_sell" else contains(g, q))
             for q in load_relationship_questions()]
    for q, found in rows:
        expected = set(q["expected"])
        precision, recall = score(found, expected)
        failures += found != expected
        print(f"{q['id']:<12} {'graph':<10} {len(found):>5} {len(found & expected):>5} {len(expected):>8} "
              f"{fmt(precision):>9} {fmt(recall):>6}")
    return 1 if failures else 0


def check(config):
    """Build the store from nothing, load the same release again, and require that the store does not change; then
    answer every question from the store as it was written to disk, so that what is checked is the artefact."""
    kg = load_knowledge()
    listings = link_listings(load_listings(config), kg.products)
    STORE_PATH.unlink(missing_ok=True)
    g = open_store()
    add_knowledge(g, kg, listings)
    first, size = save_store(g), len(g)
    g = open_store()
    add_knowledge(g, kg, listings)
    second = save_store(g)
    version = load_schema()["version"]
    print(f"release {version}: {size} triples, {len(g)} after a second load; sha256 {first[:16]}, then {second[:16]}")
    if second != first:
        print("The second load changed the store: some resource is not named by its key.", file=sys.stderr)
        return 1
    return ask(open_store())


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
    batch = load_batch(batch_path)
    violations = check_against_schema(kg)
    print(f"batch {batch_path}: {len(batch)} products added in memory; the graph has {len(g)} triples")
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
    closed = Graph()
    for triple in g:
        closed.add(triple)
    for triple in ontology:
        closed.add(triple)
    DeductiveClosure(OWLRL_Semantics).expand(closed)

    def about_the_shop(s, p, o):
        vocabulary = str(p).startswith(str(SHOP)) or (p == RDF.type and str(o).startswith((str(SHOP), str(SKOS))))
        return vocabulary and str(s).startswith(str(SHOP)) and s not in ontology.subjects()

    added = {t for t in closed if t not in stated and t not in ontology and about_the_shop(*t)}
    restated = {t for t in stated if about_the_shop(*t)}
    kinds = {}
    for s, p, o in added:
        kind = f"a {short(o)}" if p == RDF.type else short(p)
        kinds[kind] = kinds.get(kind, 0) + 1
    print(f"stated in the graph about the shop's resources: {len(restated)} statements")
    print(f"added by the reasoner: {len(added)} statements")
    for kind, n in sorted(kinds.items()):
        print(f"  {kind:<20} {n:>5}")
    print("A reasoner adds statements. It does not report an error: a product with no name is not a contradiction.")
    return 0


def validate(config, batch_path=None):
    """Check every resource against the shapes. Returns 0 when the graph conforms, 1 when it does not."""
    from pyshacl import validate as shacl_validate
    from rdflib.namespace import SH

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
    parser = argparse.ArgumentParser(description="The shop's knowledge layer as an RDF graph.")
    parser.add_argument("command", choices=["load", "count", "ask", "check", "try", "infer", "validate"])
    parser.add_argument("--batch", help="a supplier's batch, added to the knowledge in memory")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "WARNING"))
    config = load_config()
    if args.command == "load":
        load(config)
        return 0
    if args.command == "check":
        return check(config)
    if args.command == "try":
        if not args.batch:
            print("shop-kg try needs a batch: shop-kg try --batch incoming/harbour-cookware.csv", file=sys.stderr)
            return 2
        return try_batch(config, args.batch)
    if args.command == "infer":
        return infer(config, args.batch)
    if args.command == "validate":
        return validate(config, args.batch)
    if not STORE_PATH.exists():
        print(f"No graph store yet at {STORE_PATH}: run make kg-load first.", file=sys.stderr)
        return 1
    g = open_store()
    if args.command == "count":
        count(g)
        return 0
    return ask(g)


if __name__ == "__main__":
    sys.exit(main())
