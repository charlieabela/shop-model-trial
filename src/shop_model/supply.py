"""Accepting a supplier's batch into the shop's knowledge (Session 8, Stage 19).

    shop-supply plan     what would be accepted from a batch, row by row (--batch is required)
    shop-supply accept   write the accepted products to kg/, with where each one came from (--batch is required)
    shop-supply source   which supplier and batch each product came from, read from the store
    shop-supply propose  ask a language model to propose a name, category, material and range for each row of a
                         batch, from the supplier's description (--batch is required)
    shop-supply publish  load the store into GraphDB, in a named graph for the release

Three files decide what is accepted, and each is changed only through a pull request:

    kg/supplier_map.csv                   which product of the shop each row of a batch is: a new one, or one the
                                          shop already has
    incoming/<batch>.corrections.csv      the values a person corrected, with the reason for each
    kg/shapes.ttl                         what every accepted product must have

The language model proposes; it decides nothing. make ci never calls it.
"""
import argparse
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
import yaml
from rdflib import RDF, RDFS, XSD, Graph, Literal

from shop_model.config import load_config
from shop_model.graph import SHOP, STORE_PATH, add_knowledge, open_store, product_iri
from shop_model.intake import SHAPES_PATH, load_batch, with_batch
from shop_model.knowledge import link_listings, load_knowledge, load_listings, load_schema
from shop_model.logging_setup import setup_logging

MAP_PATH = Path("kg/supplier_map.csv")
SOURCES_PATH = Path("kg/sources.csv")
SUPPLIERS_PATH = Path("kg/suppliers.yaml")
PRODUCTS_PATH = Path("kg/products.csv")
PRODUCT_CATEGORIES_PATH = Path("kg/product_categories.csv")
FIELDS = ["name", "category", "material", "range"]
SOURCE_COLUMNS = ["product_id", "supplier", "batch", "supplier_key", "action"]


def supplier_iri(supplier_id):
    return SHOP[f"supplier/{supplier_id}"]


def batch_iri(batch_id):
    return SHOP[f"batch/{batch_id}"]


def load_suppliers(path=SUPPLIERS_PATH):
    if not path.exists():
        return {"suppliers": [], "batches": []}
    with open(path) as f:
        return yaml.safe_load(f)


def load_sources(path=SOURCES_PATH):
    if not path.exists():
        return pd.DataFrame(columns=SOURCE_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def batch_of(batch_path):
    """The entry in kg/suppliers.yaml for a batch file."""
    for batch in load_suppliers()["batches"]:
        if Path(batch["file"]) == Path(batch_path):
            return batch
    raise ValueError(f"{batch_path} is not a batch in {SUPPLIERS_PATH}: add it there first.")


def corrections_path(batch_path):
    return Path(batch_path).with_suffix(".corrections.csv")


def plan(batch_path):
    """Every row of a batch with what will be done with it: its key in the shop, the values after the reviewed
    corrections, and whether it is a new product or one the shop already has."""
    batch = batch_of(batch_path)
    rows = load_batch(batch_path).rename(columns={"product_id": "supplier_key"})
    mapping = pd.read_csv(MAP_PATH, dtype=str, keep_default_na=False)
    mapping = mapping[mapping["supplier"] == batch["supplier"]]
    unmapped = sorted(set(rows["supplier_key"]) - set(mapping["supplier_key"]))
    if unmapped:
        raise ValueError(f"Rows of {batch_path} with no entry in {MAP_PATH}: {unmapped}")
    rows = rows.merge(mapping[["supplier_key", "action", "product_id"]], on="supplier_key")
    rows["corrected"] = ""
    path = corrections_path(batch_path)
    if path.exists():
        for c in pd.read_csv(path, dtype=str, keep_default_na=False).itertuples():
            match = rows["supplier_key"] == c.supplier_key
            rows.loc[match, c.field] = c.value
            rows.loc[match, "corrected"] = (rows.loc[match, "corrected"] + " " + c.field).str.strip()
    return rows, batch


def show(rows):
    print(f"{'supplier key':<32} {'action':<6} {'key in the shop':<26} corrected")
    for r in rows.itertuples():
        print(f"{r.supplier_key:<32} {r.action:<6} {r.product_id:<26} {r.corrected}")


def conforms(kg, config):
    """Whether the knowledge, as a graph, conforms to the shapes; and the violations if it does not."""
    from pyshacl import validate as shacl_validate
    from rdflib.namespace import SH

    listings = link_listings(load_listings(config), kg.products)
    g = add_knowledge(Graph(), kg, listings)
    ok, report, _ = shacl_validate(g, shacl_graph=Graph().parse(SHAPES_PATH, format="turtle"), inference="none")
    return ok, [str(m) for m in report.objects(None, SH.resultMessage)]


def append_rows(path, rows):
    """Add rows to the end of a CSV file, leaving every existing line as it is."""
    text = path.read_text() if path.exists() else ""
    with open(path, "a", newline="") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        csv.writer(f, lineterminator="\n").writerows(rows)


def accept(config, batch_path):
    """Write the accepted products of a batch to kg/. Nothing is written unless the result conforms to the shapes.
    Running it again changes nothing: a product already in kg/ is not added twice."""
    rows, batch = plan(batch_path)
    show(rows)
    kg = load_knowledge()
    new = rows[(rows["action"] == "add") & ~rows["product_id"].isin(kg.products["product_id"])]
    ok, violations = conforms(with_batch(kg, new), config)
    if not ok:
        print(f"Not accepted: the result does not conform to {SHAPES_PATH}.", file=sys.stderr)
        for violation in sorted(set(violations)):
            print(f"  {violation}", file=sys.stderr)
        return 1
    sources = load_sources()
    recorded = set(zip(sources["product_id"], sources["batch"]))
    source_rows = [[r.product_id, batch["supplier"], batch["id"], r.supplier_key, r.action]
                   for r in rows.itertuples() if (r.product_id, batch["id"]) not in recorded]
    append_rows(PRODUCTS_PATH, [[r["product_id"], r["class"], r["name"], r["material"], r["range"]]
                                for r in new.to_dict("records")])
    append_rows(PRODUCT_CATEGORIES_PATH, [[r.product_id, r.category] for r in new.itertuples()])
    if source_rows and not SOURCES_PATH.exists():
        append_rows(SOURCES_PATH, [SOURCE_COLUMNS])
    append_rows(SOURCES_PATH, source_rows)
    already = int((rows["action"] == "add").sum()) - len(new)
    print(f"{len(new)} products written to kg/; {already} were there already; "
          f"{int((rows['action'] == 'same').sum())} are products the shop already has")
    return 0


def add_sources(g, kg):
    """Add to the graph where each product came from: its supplier, its batch and the key the supplier gave it.
    Called by add_knowledge. A release with no kg/sources.csv, such as 1.0, gets no triples from here."""
    sources = load_sources()
    sources = sources[sources["product_id"].isin(kg.products["product_id"])]
    if sources.empty:
        return g
    registry = load_suppliers()
    for s in registry["suppliers"]:
        node = supplier_iri(s["id"])
        g.add((node, RDF.type, SHOP.Supplier))
        g.add((node, RDFS.label, Literal(s["name"], lang="en")))
    for b in registry["batches"]:
        node = batch_iri(b["id"])
        g.add((node, RDF.type, SHOP.Batch))
        g.add((node, SHOP.receivedFrom, supplier_iri(b["supplier"])))
        g.add((node, SHOP.received, Literal(b["received"], datatype=XSD.date)))
    for r in sources.itertuples():
        node = product_iri(r.product_id)
        g.add((node, SHOP.suppliedBy, supplier_iri(r.supplier)))
        g.add((node, SHOP.inBatch, batch_iri(r.batch)))
        g.add((node, SHOP.supplierKey, Literal(r.supplier_key)))
    return g


SOURCE_QUERY = """
PREFIX shop: <https://ari3217.example.org/shop/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?id ?supplier ?received ?key WHERE {
  ?product shop:productId ?id ; shop:suppliedBy/rdfs:label ?supplier ; shop:inBatch/shop:received ?received ;
           shop:supplierKey ?key .
}
ORDER BY ?id
"""


def source(product_id=None):
    """Which supplier and batch each product came from, read from the store."""
    if not STORE_PATH.exists():
        print(f"No store yet at {STORE_PATH}: run make kg-load first.", file=sys.stderr)
        return 1
    rows = [r for r in open_store().query(SOURCE_QUERY) if product_id in (None, str(r.id))]
    print(f"{'product':<26} {'supplier':<18} {'received':<11} supplier key")
    for r in rows:
        print(f"{str(r.id):<26} {str(r.supplier):<18} {str(r.received):<11} {r.key}")
    print(f"{len(rows)} products with a recorded source")
    return 0


PROMPT_VERSION = "1"
PROMPT = """You classify products for a shop. Reply with one JSON object and nothing else, with the keys
"name", "category", "material" and "range".
- "name": a short product name taken from the description.
- "category": one of: {categories}
- "material": one of: {materials}
- "range": "premium" if the description says premium line, otherwise "core".
Description: {description}"""


def chat(llm, prompt):
    """One request to an OpenAI-compatible endpoint. The address and the model come from config/config.yaml."""
    body = {"model": llm["model"], "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    request = urllib.request.Request(
        llm["base_url"].rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'not-needed')}"})
    with urllib.request.urlopen(request, timeout=llm.get("timeout_seconds", 120)) as response:
        return json.load(response)["choices"][0]["message"]["content"]


def propose(config, batch_path):
    """Ask the model for the fields of each row, and show where its proposal differs from the batch. The proposals
    are written beside the batch for a person to read. They are not used by accept or by make ci."""
    llm = config["llm"]
    kg = load_knowledge()
    batch = load_batch(batch_path)
    categories = ", ".join(sorted(c for c, v in kg.concepts.items() if "is_a" in v))
    materials = ", ".join(sorted(kg.materials))
    out, unreadable, different = [], 0, 0
    print(f"model {llm['model']} at {llm['base_url']}; prompt version {PROMPT_VERSION}")
    for r in batch.itertuples():
        reply = chat(llm, PROMPT.format(categories=categories, materials=materials, description=r.description))
        try:
            proposal = json.loads(reply)
            values = {field: str(proposal.get(field, "")) for field in FIELDS}
        except (json.JSONDecodeError, AttributeError):
            unreadable += 1
            values = dict.fromkeys(FIELDS, "")
        for field in FIELDS:
            given = getattr(r, field)
            if values[field] and values[field] != given:
                different += 1
                print(f"{r.product_id:<32} {field:<9} batch: {given or '(empty)':<28} proposed: {values[field]}")
        out.append({"supplier_key": r.product_id, **values})
    path = Path(batch_path).with_suffix(".proposed.csv")
    pd.DataFrame(out).to_csv(path, index=False)
    print(f"{len(batch)} rows; {different} proposed values differ from the batch; {unreadable} replies were not "
          f"valid JSON; written to {path}")
    return 0


def publish(config):
    """Load the store into GraphDB, replacing the named graph of this release, and count what GraphDB then holds."""
    if not STORE_PATH.exists():
        print(f"No store yet at {STORE_PATH}: run make kg-check first.", file=sys.stderr)
        return 1
    gdb = config["graphdb"]
    version = load_schema()["version"]
    graph = str(SHOP[f"release/{version}"])
    repository = f"{gdb['url'].rstrip('/')}/repositories/{gdb['repository']}"
    put = urllib.request.Request(
        f"{repository}/rdf-graphs/service?graph={urllib.parse.quote(graph, safe='')}",
        data=STORE_PATH.read_bytes(), method="PUT", headers={"Content-Type": "application/n-triples"})
    with urllib.request.urlopen(put, timeout=120):
        pass
    query = f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"
    get = urllib.request.Request(f"{repository}?query={urllib.parse.quote(query)}",
                                 headers={"Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(get, timeout=120) as response:
        count = int(json.load(response)["results"]["bindings"][0]["n"]["value"])
    local = len(open_store())
    print(f"release {version} published to <{graph}> in the repository {gdb['repository']}")
    print(f"triples in the store: {local}; triples in the named graph: {count}")
    return 0 if count == local else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Accepting a supplier's batch into the shop's knowledge.")
    parser.add_argument("command", choices=["plan", "accept", "source", "propose", "publish"])
    parser.add_argument("--batch", help="a supplier's batch, listed in kg/suppliers.yaml")
    parser.add_argument("--product", help="for source: one product, by its key")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "WARNING"))
    config = load_config()
    if args.command in ("plan", "accept", "propose") and not args.batch:
        print(f"A batch is needed: shop-supply {args.command} --batch incoming/harbour-cookware.csv",
              file=sys.stderr)
        return 2
    if args.command == "plan":
        show(plan(args.batch)[0])
        return 0
    if args.command == "accept":
        return accept(config, args.batch)
    if args.command == "source":
        return source(args.product)
    if args.command == "propose":
        return propose(config, args.batch)
    return publish(config)


if __name__ == "__main__":
    sys.exit(main())
