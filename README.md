# Shop model

Predicts whether an item in the shop will sell, from its price.

## Requirements

Python 3.12 with the venv module. On Ubuntu:

```bash
sudo apt install python3 python3-venv
```

## Install

Run these from this folder, the one with pyproject.toml:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e ".[dev]"
cp env.example .env
```

## Test

```bash
pytest
```

## Train

```bash
shop-train
```

The model is saved to models/model.joblib. Shared settings are in
config/config.yaml; settings for your machine go in .env.


## Monitoring

The monitoring services (Prometheus and Grafana) are in the monitoring
folder and start together with the API and the database:

```bash
docker compose up -d --build
docker compose ps
```

Four containers should be running: api, db, prometheus and grafana.

- Dashboard (Grafana): http://localhost:3000, opens on the Model service
  dashboard, no login needed
- Prometheus: http://localhost:9090 (Status, then Target health, should
  show model-api as UP)
- The API's own metrics: http://localhost:8888/metrics

In a VirtualBox VM, ports 3000 and 9090 need port-forwarding rules, as
for 8888.

## Generating traffic

With the virtual environment active, from this folder:

```bash
python3 monitoring/traffic.py --field price --mean 23 --sd 12 --duration 300
```

To simulate drift (prices rising), run it again with a higher mean:

```bash
python3 monitoring/traffic.py --field price --mean 35 --sd 12 --duration 300
```

Stop it early with Ctrl+C. `python3 monitoring/traffic.py --help` lists
all options.

## Stopping

```bash
docker compose down
```

This keeps the stored predictions and metrics, which live in volumes.
`docker compose down -v` deletes them as well.

## The pipeline (Session 5, Stage 12)

Every check runs with one command, in order, stopping at the first failure:

```
make install    # pinned requirements, then the package with its dev tools
cp env.example .env
make ci         # lint, test, validate, gate, build
```

| Target | What it does | Fails when |
|---|---|---|
| `make lint` | ruff checks the code | a style or correctness rule is broken |
| `make test` | pytest, including the property-based range test | any test fails |
| `make validate` | checks `data/prices_v2.csv` and `data/holdout.csv` against the schema | a row is invalid or a column is unexpected |
| `make gate` | trains on rows before the holdout and scores the frozen holdout | exit 1: below the threshold; exit 2: the holdout changed |
| `make build` | builds `shop-model:<version>` and `shop-model:<version>-<commit>`, the version read from `pyproject.toml` | the image does not build |

Compare the gate with the random split of Stage 7: `make gate GATE_SPLIT=random`.

The gate's metric and threshold live in `config/config.yaml` (`gate_metric`, `gate_threshold`), next to the holdout's start date and SHA-256 fingerprint. Never edit `data/holdout.csv`: the gate refuses to run if its fingerprint changes.

Start the whole stack with the version from `pyproject.toml`: `make up`.

## Continuous integration (Session 5, Stage 13)

`.github/workflows/ci.yml` runs `make ci` on every push and pull request. On a push to `main`, once every check has passed, the same run publishes the image it just built to GitHub's container registry, as `ghcr.io/<owner>/shop-model:<version>` and `:<version>-<commit>`.

- Require the `ci` check before merging (repository settings, branch protection), so a failing gate blocks the merge.
- The version lives only in `pyproject.toml`. To release, change it there; the image tag and `MODEL_VERSION` follow.
- No GitHub Actions? Run the same workflow locally with `act -j ci` and keep its log.
- GitLab users: `.gitlab-ci.yml` runs the same `make` targets.

## Tracking and the registry (Session 6, Stage 14)

MLflow runs as one more service, on port 5000, with its records in Postgres and its files in the `mlflow-artifacts` volume.

```
docker compose up -d db mlflow     # add --profile monitoring for Prometheus and Grafana
make train                         # train, score the frozen holdout, record the run
make register                      # train, then register that run: only if it passed the gate, from committed code
shop-registry alias champion 2     # point the champion alias at version 2
shop-registry show                 # versions, gate metric and aliases
```

Each run records the seed, VAT rate, training window, every holdout metric, the git commit, and the SHA-256 of the data and the holdout. `MLFLOW_TRACKING_URI` in `.env` says where runs go.

On an existing database volume, create MLflow's database once: `docker compose exec db createdb -U shop mlflow`.

## Serving from the registry (Session 6, Stage 15)

The image holds code only. At start-up the API loads `models:/shop_model@champion` and records which version it serves.

- `/health`: the process is alive.
- `/ready`: a model is loaded; answers 503 until then, with `model_version` once ready.
- Each stored prediction records the model version, for example `shop_model/2`.

Roll back by moving the alias and restarting the API: `make champion V=1`. No rebuild.

To work without the registry on your own machine, outside compose, set `MODEL_URI=models/model.joblib` (written by `shop-train`) and run uvicorn directly. The image holds no model file, so inside compose the API always needs the registry.

The model card is in `docs/model_card.md`; attach it to a version with `shop-registry card 2 docs/model_card.md`.

## The shop's knowledge (Session 7)

The listings say what was listed, at what price, and whether it sold. What a product is (its categories, its
material, its range, the words staff use for it) lives in kg/, as release v1.0 of the shop's knowledge, the
Christmas 2026 catalogue. The catalogue also holds products that have not been listed yet.

| File | What it holds |
|---|---|
| kg/schema.yaml | the classes, properties and relationships, with cardinalities; every file conforms to it |
| kg/products.csv | one row per product, listed or not yet: identifier, class (Product or GiftSet), name, material, range |
| kg/product_categories.csv | which category each product is in; a product can be in more than one |
| kg/gift_sets.csv | which products each gift set contains |
| kg/taxonomy.yaml | departments, categories and subcategories, each a kind of its broader concept |
| kg/materials.yaml | materials, each a kind of its broader material (oak is a wood) |
| kg/complements.yaml | rules saying which categories go with which; pairs of products are derived from them |
| kg/synonyms.yaml | the words staff use, each mapped to a concept, a range or a material |
| kg/questions.yaml | the competency questions, with the answers the shop expects |

```bash
make profile                 # listings against products, and the catalogue
make ask                     # every question, through both routes, scored
make ask ROUTE=keyword SHOW=--show
make sales BY=department     # listings, sales and sell-through rate by department, material or range
```

The decision to add this layer is recorded in docs/adr/0001-knowledge-layer.md.

### Releasing the knowledge

The knowledge has its own version, in `kg/schema.yaml`, separate from the code's version in `pyproject.toml`.
A change to kg/ goes through a pull request like any change to the code: the `ci` check runs the schema and
the competency questions, and the branch rule from Session 5 blocks a merge that fails them.

```bash
make kg-version              # the version of the knowledge, from kg/schema.yaml
make kg-release              # run the tests, then tag the commit kg-v<version>; refuses uncommitted changes
git push origin kg-v1.0      # publish the tag
```

A tag is never reused. To release a change, raise the version in `kg/schema.yaml` (1.1 for added or corrected
knowledge, 2.0 for a change to the schema that existing files or queries depend on), commit, and release again.

## The knowledge graph (Session 7, Stage 17)

The same knowledge as an RDF graph, built with rdflib into a store of N-Triples, kg/build/shop.nt. The store is
derived from kg/ and the listings, so it is not committed; rebuild it at any time.

```bash
make kg-load      # add the knowledge files and the listings to the store; safe to run again
make kg-count     # triples, instances of each class, uses of each property, and the store's SHA-256
make kg-ask       # the staff questions in SPARQL, including the cross-sell and gift-set questions, scored
                  # against kg/questions.yaml
make kg-clean     # empty the store, for a rebuild from the files
```

`make ci` now runs `make kg-check`: it builds the store from nothing, loads it a second time and requires the same
SHA-256, then answers every question from the store as written and fails on any wrong answer. From this stage
`make kg-release` runs the same check and records the store's SHA-256 in the tag's message (`git tag -n kg-v1.0`),
so a rollback can be confirmed: check out the files of an earlier release, run `make kg-check`, and compare.

```bash
git checkout kg-v1.0 -- kg/   # the knowledge files of release 1.0, on your current branch
make kg-check                 # the SHA-256 printed must match the one in the tag's message
```

## The ontology and the shapes (Session 8, Stage 18)

A supplier, Harbour Cookware, has sent a batch of products: `incoming/harbour-cookware.csv`. Fictitious, written
for this unit. The three commands below work on the knowledge with the batch added in memory. None of them
changes `kg/` or the store.

```bash
make kg-try BATCH=incoming/harbour-cookware.csv        # what Stage 17 can say about the batch
make kg-infer                                          # what a reasoner adds, given kg/ontology.ttl
make kg-validate                                       # kg/ checked against kg/shapes.ttl; part of make ci
make kg-validate BATCH=incoming/harbour-cookware.csv   # the batch checked against the shapes
```

- `kg/ontology.ttl` is the schema of `kg/schema.yaml` written in RDFS and OWL. It says what the words mean.
- `kg/shapes.ttl` holds the SHACL shapes. They say what every product, gift set, category, material and
  listing must have.

The knowledge is still release 1.0: the batch has not been accepted.
