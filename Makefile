# The pipeline as a program (Session 5). Every target is a command you already know.
# Recipes must start with a TAB, not spaces.

# One version, in one place: pyproject.toml. Everything else reads it.
VERSION := $(shell python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")
IMAGE ?= shop-model
# Each image also carries the commit it was built from, so a tag is never reused for different code.
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo nogit)
# On GitHub the workflow passes SOURCE, the repository address. The label links the image to the repository,
# and ghcr.io refuses a push from the workflow without it.
SOURCE ?=
LABELS := $(if $(SOURCE),--label org.opencontainers.image.source=$(SOURCE))
GATE_SPLIT ?= holdout

.PHONY: install lint test validate gate build publish up version ci train register champion ask profile sales kg-version kg-release \
	kg-load kg-count kg-ask kg-clean kg-check kg-try kg-infer kg-validate \
	kg-plan kg-accept kg-source kg-propose kg-publish

install:
	python3 -m pip install -r requirements.txt
	python3 -m pip install --no-deps -e ".[dev]"

lint:
	ruff check src tests

test:
	pytest -q

validate:
	shop-validate

gate:
	shop-gate --split $(GATE_SPLIT)

build:
	docker build --build-arg APP_VERSION=$(VERSION) -t $(IMAGE):$(VERSION) -t $(IMAGE):$(VERSION)-$(GIT_SHA) $(LABELS) .

publish:
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):$(VERSION)-$(GIT_SHA)

up:
	APP_VERSION=$(VERSION) docker compose up -d --build

# Session 6: train and track a run, then register it only if it passed the gate.
train:
	shop-train

register: train
	shop-registry register

# Point the champion alias at version V and restart the API, which loads it at start-up.
# Rolling back is the same command with the previous version: make champion V=1
champion:
	shop-registry alias champion $(V)
	docker compose restart api

# Session 7: the staff questions, answered through the listings alone and through the shop's knowledge.
# ROUTE=keyword or ROUTE=knowledge runs one route; SHOW=--show lists what each route got wrong or missed.
ROUTE ?= both
SHOW ?=
ask:
	shop-ask questions --route $(ROUTE) $(SHOW)

profile:
	shop-ask profile

# Session 7: sales and sell-through rate by department, material or range, from the knowledge layer.
BY ?= department
sales:
	shop-ask sales --by $(BY)

# Session 7: the knowledge has a version of its own, kept in one place, kg/schema.yaml, and separate from the code's.
# A release of the knowledge is an annotated git tag on a commit whose tests pass, and a tag is never reused:
# git refuses to create one that exists. Push it with: git push origin kg-v$(KG_VERSION)
KG_VERSION := $(shell sed -n 's/^version: "\(.*\)"$$/\1/p' kg/schema.yaml)
kg-version:
	@echo $(KG_VERSION)

# From Stage 17 the release is checked as a graph too, and the tag records the fingerprint of the store it gives.
STORE_SHA = $$(sha256sum kg/build/shop.nt | cut -c1-16)
kg-release: test kg-check
	@git diff --quiet HEAD || \
		{ echo "Commit your changes before releasing the knowledge."; exit 1; }
	git tag -a kg-v$(KG_VERSION) \
		-m "Shop knowledge $(KG_VERSION); store sha256 $(STORE_SHA)"
	@echo "Push the tag with: git push origin kg-v$(KG_VERSION)"

# Session 7, Stage 17: the knowledge layer as an RDF graph, in the store kg/build/shop.nt.
# kg-load is safe to run again; kg-clean empties the store, for a rebuild from the files.
kg-load:
	shop-kg load

kg-count:
	shop-kg count

kg-ask:
	shop-kg ask

kg-clean:
	rm -f kg/build/shop.nt

# What make ci checks: the store built from nothing, loaded a second time without change, and the questions
# answered from the store as written. It leaves that store in kg/build/shop.nt, the artefact that was checked.
kg-check:
	shop-kg check

# Session 8, Stage 18: the ontology, the shapes and a supplier's batch. None of these targets changes kg/ or the
# store. With BATCH=incoming/harbour-cookware.csv each one works on the knowledge with that batch added in memory.
BATCH ?=

# What Stage 17 can say about a batch: make kg-try BATCH=incoming/harbour-cookware.csv
kg-try:
	shop-intake try --batch $(BATCH)

# What a reasoner adds, given kg/ontology.ttl.
kg-infer:
	shop-intake infer $(if $(BATCH),--batch $(BATCH))

# Every resource checked against kg/shapes.ttl. make ci runs it on kg/, with no batch.
kg-validate:
	shop-intake validate $(if $(BATCH),--batch $(BATCH))

# Session 8, Stage 19: accepting a batch, and publishing a release.
# What would be accepted from a batch, row by row: make kg-plan BATCH=incoming/harbour-cookware.csv
kg-plan:
	shop-supply plan --batch $(BATCH)

# Write the accepted products to kg/. Safe to run again: a product already in kg/ is not added twice.
kg-accept:
	shop-supply accept --batch $(BATCH)

# Where each product came from, read from the store: make kg-source, or make kg-source PRODUCT=copper-stock-pot
PRODUCT ?=
kg-source:
	shop-supply source $(if $(PRODUCT),--product $(PRODUCT))

# Ask the language model to propose the fields of each row of a batch. Needs the model; make ci never runs it.
kg-propose:
	shop-supply propose --batch $(BATCH)

# Load the checked store into GraphDB, in a named graph for the release. Needs GraphDB; make ci never runs it.
kg-publish: kg-check
	shop-supply publish

version:
	@echo $(VERSION)

# The whole pipeline, in order. make stops at the first command that exits non-zero.
ci: lint test validate gate kg-check kg-validate build
