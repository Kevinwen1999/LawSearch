-- Tribunal "citations" can be file numbers, and one Immigration and Refugee Board file
-- carries separate RPD and RAD decisions (e.g. TB6-11632: RAD 2016, RPD rehearing 2019).
-- A citation is unique within a court, not across courts.

DROP INDEX cases_citation_key;
CREATE UNIQUE INDEX cases_court_citation_key ON cases (court, citation) WHERE citation IS NOT NULL;
CREATE INDEX cases_citation_idx ON cases (citation);
