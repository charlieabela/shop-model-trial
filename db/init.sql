-- Runs once, when the database is first created.
CREATE TABLE predictions (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now (),
    price DOUBLE PRECISION NOT NULL,
    probability DOUBLE PRECISION NOT NULL,
    model_version TEXT NOT NULL
);

-- MLflow keeps its records in a database of its own, on the same server (Session 6).
CREATE DATABASE mlflow;
