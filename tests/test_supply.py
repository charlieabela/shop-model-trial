"""Accepting a supplier's batch (Session 8, Stage 19)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pandas as pd
import pytest
from rdflib import RDF, Graph

from shop_model.config import load_config
from shop_model.graph import SHOP, add_knowledge
from shop_model.knowledge import link_listings, load_knowledge, load_listings
from shop_model.supply import MAP_PATH, accept, load_sources, load_suppliers, plan, propose, publish

BATCH = "incoming/harbour-cookware.csv"


@pytest.fixture(scope="module")
def config():
    return load_config()


@pytest.fixture(scope="module")
def graph(config):
    kg = load_knowledge()
    return add_knowledge(Graph(), kg, link_listings(load_listings(config), kg.products))


def serve(reply):
    """A local server that answers every request with one JSON reply, standing in for the model or for GraphDB."""
    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = do_PUT = respond

        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_every_row_of_the_batch_is_in_the_supplier_map():
    rows, batch = plan(BATCH)
    assert batch["supplier"] == "harbour-cookware"
    assert list(rows["action"]).count("add") == 7 and list(rows["action"]).count("same") == 1


def test_the_supplier_map_names_only_known_suppliers():
    known = {s["id"] for s in load_suppliers()["suppliers"]}
    assert set(pd.read_csv(MAP_PATH, dtype=str)["supplier"]) <= known


def test_the_corrections_are_applied():
    rows = plan(BATCH)[0].set_index("supplier_key")
    assert rows.loc["copper-jam-pan", "name"] == "Copper jam pan, 9 litre"
    assert rows.loc["copper-serving-tray", "category"] == "serveware"


def test_the_duplicate_is_not_added_as_a_second_product():
    products = set(load_knowledge().products["product_id"])
    assert "cast-iron-casserole" in products and "enamelled-cast-iron-casserole" not in products


def test_accepting_the_batch_again_changes_no_file(config, capsys):
    files = [Path("kg/products.csv"), Path("kg/product_categories.csv"), Path("kg/sources.csv")]
    before = [f.read_bytes() for f in files]
    assert accept(config, BATCH) == 0
    assert [f.read_bytes() for f in files] == before
    assert "0 products written" in capsys.readouterr().out


def test_every_product_from_the_batch_has_its_source_in_the_graph(graph):
    supplied = {str(s) for s in graph.subjects(SHOP.suppliedBy, SHOP["supplier/harbour-cookware"])}
    assert len(supplied) == len(load_sources()) == 8
    assert (SHOP["batch/harbour-cookware-2026-12"], RDF.type, SHOP.Batch) in graph


def test_the_supplier_key_is_kept_when_the_shop_gives_its_own_key(graph):
    assert str(graph.value(SHOP["product/stainless-saucepan-18"], SHOP.supplierKey)) == "HC_0518"


def test_propose_reads_the_reply_of_an_openai_compatible_endpoint(config, tmp_path, capsys):
    content = json.dumps({"name": "Copper jam pan", "category": "cookware", "material": "copper", "range": "premium"})
    server = serve({"choices": [{"message": {"content": content}}]})
    batch = tmp_path / "batch.csv"
    batch.write_text(Path(BATCH).read_text())
    llm = {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "test"}
    assert propose({**config, "llm": llm}, batch) == 0
    server.shutdown()
    assert "0 replies were not valid JSON" in capsys.readouterr().out
    assert len(pd.read_csv(tmp_path / "batch.proposed.csv")) == 8


def test_publish_fails_when_graphdb_holds_a_different_number_of_triples(config):
    if not Path("kg/build/shop.nt").exists():
        pytest.skip("no store built yet")
    server = serve({"results": {"bindings": [{"n": {"value": "1"}}]}})
    gdb = {"url": f"http://127.0.0.1:{server.server_port}", "repository": "shop"}
    assert publish({**config, "graphdb": gdb}) == 1
    server.shutdown()
