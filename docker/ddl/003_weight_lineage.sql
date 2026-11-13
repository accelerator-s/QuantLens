-- Add explicit factor identities to weight-fit evidence; existing Parquet stays immutable.
ALTER TABLE dws.factor_weights ADD COLUMNS (factor_version string, sources string);
