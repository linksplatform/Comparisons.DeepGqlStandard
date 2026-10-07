-- Deep core links table/indexes from deep-foundation/deeplinks
-- migrations/1616701513782-links.ts at 381207dda313ab3c1fed0a4bfc2ddbe631750411.
-- type_id defaults to 0 because the common benchmark contract is untyped.
-- Application triggers, permissions, computed values and materialized paths
-- belong to Deep's application layer and are outside this core-storage comparison.
CREATE TABLE public.links (
    id bigint PRIMARY KEY,
    from_id bigint DEFAULT 0,
    to_id bigint DEFAULT 0,
    type_id bigint NOT NULL DEFAULT 0
);
CREATE SEQUENCE links_id_seq AS bigint START WITH 1 INCREMENT BY 1 CACHE 1;
ALTER SEQUENCE links_id_seq OWNED BY public.links.id;
ALTER TABLE public.links ALTER COLUMN id SET DEFAULT nextval('links_id_seq'::regclass);
CREATE INDEX links__id_hash ON links USING hash (id);
CREATE INDEX links__from_id_hash ON links USING hash (from_id);
CREATE INDEX links__from_id_btree ON links USING btree (from_id);
CREATE INDEX links__to_id_hash ON links USING hash (to_id);
CREATE INDEX links__to_id_btree ON links USING btree (to_id);
CREATE INDEX links__type_id_hash ON links USING hash (type_id);
CREATE INDEX links__type_id_btree ON links USING btree (type_id);
