-- OpenActs PostgreSQL projection schema, version 1.
-- Apply once to a fresh dedicated database. A repeated application should fail
-- instead of hiding schema drift behind IF NOT EXISTS.

BEGIN;

CREATE TABLE corpus_releases (
    release_tag text PRIMARY KEY,
    commit_sha text NOT NULL,
    canonical_schema_versions text[] NOT NULL,
    projection_schema_version integer NOT NULL DEFAULT 1,
    import_state text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT corpus_releases_tag_format
        CHECK (release_tag ~ '^corpus-v[0-9]+\.[0-9]+\.[0-9]+$'),
    CONSTRAINT corpus_releases_commit_format
        CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    CONSTRAINT corpus_releases_schema_versions_present
        CHECK (cardinality(canonical_schema_versions) > 0),
    CONSTRAINT corpus_releases_projection_schema_supported
        CHECK (projection_schema_version = 1),
    CONSTRAINT corpus_releases_import_state_valid
        CHECK (import_state IN ('importing', 'ready'))
);

CREATE TABLE projection_state (
    singleton boolean PRIMARY KEY DEFAULT true,
    active_release_tag text REFERENCES corpus_releases (release_tag),
    previous_release_tag text REFERENCES corpus_releases (release_tag),
    activated_at timestamptz,
    CONSTRAINT projection_state_singleton CHECK (singleton),
    CONSTRAINT projection_state_distinct_releases CHECK (
        active_release_tag IS NULL
        OR previous_release_tag IS NULL
        OR active_release_tag <> previous_release_tag
    ),
    CONSTRAINT projection_state_previous_requires_active CHECK (
        previous_release_tag IS NULL OR active_release_tag IS NOT NULL
    ),
    CONSTRAINT projection_state_activation_time_consistent CHECK (
        (active_release_tag IS NULL AND activated_at IS NULL)
        OR (active_release_tag IS NOT NULL AND activated_at IS NOT NULL)
    )
);

INSERT INTO projection_state (singleton) VALUES (true);

CREATE TABLE sources (
    release_tag text NOT NULL,
    source_id text NOT NULL,
    schema_version text NOT NULL,
    document_title text NOT NULL,
    document_publisher text NOT NULL,
    language text NOT NULL,
    source_class text NOT NULL,
    canonical_record jsonb NOT NULL,
    CONSTRAINT sources_pkey PRIMARY KEY (release_tag, source_id),
    CONSTRAINT sources_release_fk FOREIGN KEY (release_tag)
        REFERENCES corpus_releases (release_tag) ON DELETE CASCADE,
    CONSTRAINT sources_id_format
        CHECK (source_id ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT sources_language_format CHECK (language ~ '^[a-z]{3}$'),
    CONSTRAINT sources_class_valid CHECK (source_class IN (
        'official_gazette',
        'certified_legislative_copy',
        'official_agency_copy',
        'official_translation',
        'institutional_copy',
        'secondary_copy'
    )),
    CONSTRAINT sources_record_is_object
        CHECK (jsonb_typeof(canonical_record) = 'object'),
    CONSTRAINT sources_record_type_matches CHECK (
        canonical_record ->> 'record_type' IS NOT DISTINCT FROM 'source'
    ),
    CONSTRAINT sources_record_id_matches CHECK (
        canonical_record ->> 'source_id' IS NOT DISTINCT FROM source_id
    ),
    CONSTRAINT sources_record_schema_matches CHECK (
        canonical_record ->> 'schema_version' IS NOT DISTINCT FROM schema_version
    ),
    CONSTRAINT sources_record_title_matches CHECK (
        canonical_record ->> 'document_title' IS NOT DISTINCT FROM document_title
    ),
    CONSTRAINT sources_record_publisher_matches CHECK (
        canonical_record ->> 'document_publisher'
            IS NOT DISTINCT FROM document_publisher
    ),
    CONSTRAINT sources_record_language_matches CHECK (
        canonical_record ->> 'language' IS NOT DISTINCT FROM language
    ),
    CONSTRAINT sources_record_class_matches CHECK (
        canonical_record ->> 'source_class' IS NOT DISTINCT FROM source_class
    )
);

CREATE TABLE acts (
    release_tag text NOT NULL,
    act_id text NOT NULL,
    schema_version text NOT NULL,
    jurisdiction text NOT NULL,
    country_code text NOT NULL,
    official_title text NOT NULL,
    short_title text,
    year integer NOT NULL,
    number text,
    citation text,
    text_kind text NOT NULL,
    status text NOT NULL,
    checked_through_date date,
    title_keys text[] NOT NULL,
    citation_key text,
    source_ids text[] NOT NULL,
    searchable_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', searchable_text)
    ) STORED,
    canonical_record jsonb NOT NULL,
    CONSTRAINT acts_pkey PRIMARY KEY (release_tag, act_id),
    CONSTRAINT acts_release_fk FOREIGN KEY (release_tag)
        REFERENCES corpus_releases (release_tag) ON DELETE CASCADE,
    CONSTRAINT acts_country_code_format CHECK (country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT acts_year_valid CHECK (year BETWEEN 1800 AND 9999),
    CONSTRAINT acts_text_kind_valid
        CHECK (text_kind IN ('as_enacted', 'consolidated')),
    CONSTRAINT acts_status_valid CHECK (status IN (
        'in_force',
        'repealed',
        'spent',
        'not_yet_commenced',
        'mixed',
        'unknown'
    )),
    CONSTRAINT acts_title_keys_present CHECK (cardinality(title_keys) > 0),
    CONSTRAINT acts_source_ids_present CHECK (cardinality(source_ids) > 0),
    CONSTRAINT acts_searchable_text_present CHECK (searchable_text <> ''),
    CONSTRAINT acts_record_is_object
        CHECK (jsonb_typeof(canonical_record) = 'object'),
    CONSTRAINT acts_record_type_matches CHECK (
        canonical_record ->> 'record_type' IS NOT DISTINCT FROM 'act'
    ),
    CONSTRAINT acts_record_id_matches CHECK (
        canonical_record ->> 'act_id' IS NOT DISTINCT FROM act_id
    ),
    CONSTRAINT acts_record_schema_matches CHECK (
        canonical_record ->> 'schema_version' IS NOT DISTINCT FROM schema_version
    ),
    CONSTRAINT acts_record_jurisdiction_matches CHECK (
        canonical_record ->> 'jurisdiction' IS NOT DISTINCT FROM jurisdiction
    ),
    CONSTRAINT acts_record_country_matches CHECK (
        canonical_record ->> 'country_code' IS NOT DISTINCT FROM country_code
    ),
    CONSTRAINT acts_record_official_title_matches CHECK (
        canonical_record #>> '{titles,official}'
            IS NOT DISTINCT FROM official_title
    ),
    CONSTRAINT acts_record_short_title_matches CHECK (
        canonical_record #>> '{titles,short}' IS NOT DISTINCT FROM short_title
    ),
    CONSTRAINT acts_record_year_matches CHECK (
        (canonical_record ->> 'year')::integer IS NOT DISTINCT FROM year
    ),
    CONSTRAINT acts_record_number_matches CHECK (
        canonical_record ->> 'number' IS NOT DISTINCT FROM number
    ),
    CONSTRAINT acts_record_citation_matches CHECK (
        canonical_record ->> 'citation' IS NOT DISTINCT FROM citation
    ),
    CONSTRAINT acts_record_text_kind_matches CHECK (
        COALESCE(canonical_record ->> 'text_kind', 'as_enacted')
            IS NOT DISTINCT FROM text_kind
    ),
    CONSTRAINT acts_record_status_matches CHECK (
        canonical_record ->> 'status' IS NOT DISTINCT FROM status
    ),
    CONSTRAINT acts_record_checked_through_matches CHECK (
        (canonical_record ->> 'checked_through_date')::date
            IS NOT DISTINCT FROM checked_through_date
    )
);

CREATE INDEX acts_listing_idx
    ON acts (release_tag, official_title, year DESC, act_id);
CREATE INDEX acts_citation_key_idx
    ON acts (release_tag, citation_key)
    WHERE citation_key IS NOT NULL;
CREATE INDEX acts_title_keys_idx ON acts USING GIN (title_keys);
CREATE INDEX acts_search_vector_idx ON acts USING GIN (search_vector);

CREATE TABLE provisions (
    release_tag text NOT NULL,
    provision_id text NOT NULL,
    act_id text NOT NULL,
    schema_version text NOT NULL,
    parent_provision_id text,
    sibling_order integer NOT NULL,
    sequence integer NOT NULL,
    depth integer NOT NULL,
    node_type text NOT NULL,
    display_label text,
    heading text,
    text_fidelity text NOT NULL,
    reference_key text NOT NULL,
    source_ids text[] NOT NULL,
    searchable_text text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', searchable_text)
    ) STORED,
    canonical_record jsonb NOT NULL,
    CONSTRAINT provisions_pkey PRIMARY KEY (release_tag, provision_id),
    CONSTRAINT provisions_release_act_id_unique
        UNIQUE (release_tag, act_id, provision_id),
    CONSTRAINT provisions_release_sequence_unique
        UNIQUE (release_tag, act_id, sequence),
    CONSTRAINT provisions_reference_key_unique
        UNIQUE (release_tag, act_id, reference_key),
    CONSTRAINT provisions_sibling_order_unique
        UNIQUE NULLS NOT DISTINCT (
            release_tag,
            act_id,
            parent_provision_id,
            sibling_order
        ),
    CONSTRAINT provisions_release_fk FOREIGN KEY (release_tag)
        REFERENCES corpus_releases (release_tag) ON DELETE CASCADE,
    CONSTRAINT provisions_act_fk FOREIGN KEY (release_tag, act_id)
        REFERENCES acts (release_tag, act_id),
    CONSTRAINT provisions_parent_fk FOREIGN KEY (
        release_tag,
        act_id,
        parent_provision_id
    ) REFERENCES provisions (release_tag, act_id, provision_id),
    CONSTRAINT provisions_id_belongs_to_act
        CHECK (provision_id LIKE act_id || ':%'),
    CONSTRAINT provisions_sibling_order_positive CHECK (sibling_order >= 1),
    CONSTRAINT provisions_sequence_positive CHECK (sequence >= 1),
    CONSTRAINT provisions_depth_valid CHECK (depth >= 0),
    CONSTRAINT provisions_parent_depth_consistent CHECK (
        (parent_provision_id IS NULL AND depth = 0)
        OR (parent_provision_id IS NOT NULL AND depth > 0)
    ),
    CONSTRAINT provisions_fidelity_valid CHECK (text_fidelity IN (
        'machine_extracted',
        'single_reviewed',
        'double_reviewed',
        'source_conflict'
    )),
    CONSTRAINT provisions_reference_key_present CHECK (reference_key <> ''),
    CONSTRAINT provisions_source_ids_present CHECK (cardinality(source_ids) > 0),
    CONSTRAINT provisions_record_is_object
        CHECK (jsonb_typeof(canonical_record) = 'object'),
    CONSTRAINT provisions_record_type_matches CHECK (
        canonical_record ->> 'record_type' IS NOT DISTINCT FROM 'provision'
    ),
    CONSTRAINT provisions_record_id_matches CHECK (
        canonical_record ->> 'provision_id'
            IS NOT DISTINCT FROM provision_id
    ),
    CONSTRAINT provisions_record_schema_matches CHECK (
        canonical_record ->> 'schema_version' IS NOT DISTINCT FROM schema_version
    ),
    CONSTRAINT provisions_record_parent_matches CHECK (
        canonical_record ->> 'parent_provision_id'
            IS NOT DISTINCT FROM parent_provision_id
    ),
    CONSTRAINT provisions_record_order_matches CHECK (
        (canonical_record ->> 'order')::integer
            IS NOT DISTINCT FROM sibling_order
    ),
    CONSTRAINT provisions_record_node_type_matches CHECK (
        canonical_record ->> 'node_type' IS NOT DISTINCT FROM node_type
    ),
    CONSTRAINT provisions_record_label_matches CHECK (
        canonical_record ->> 'display_label'
            IS NOT DISTINCT FROM display_label
    ),
    CONSTRAINT provisions_record_heading_matches CHECK (
        canonical_record ->> 'heading' IS NOT DISTINCT FROM heading
    ),
    CONSTRAINT provisions_record_fidelity_matches CHECK (
        canonical_record ->> 'text_fidelity'
            IS NOT DISTINCT FROM text_fidelity
    )
);

CREATE INDEX provisions_label_idx
    ON provisions (release_tag, act_id, display_label)
    WHERE display_label IS NOT NULL;
CREATE INDEX provisions_search_vector_idx
    ON provisions USING GIN (search_vector);

CREATE TABLE citations (
    release_tag text NOT NULL,
    citation_id text NOT NULL,
    schema_version text NOT NULL,
    source_provision_id text NOT NULL,
    source_block_id text NOT NULL,
    target_act_id text NOT NULL,
    target_provision_id text,
    canonical_record jsonb NOT NULL,
    CONSTRAINT citations_pkey PRIMARY KEY (release_tag, citation_id),
    CONSTRAINT citations_release_fk FOREIGN KEY (release_tag)
        REFERENCES corpus_releases (release_tag) ON DELETE CASCADE,
    CONSTRAINT citations_source_provision_fk FOREIGN KEY (
        release_tag,
        source_provision_id
    ) REFERENCES provisions (release_tag, provision_id),
    CONSTRAINT citations_target_act_fk FOREIGN KEY (release_tag, target_act_id)
        REFERENCES acts (release_tag, act_id),
    CONSTRAINT citations_target_provision_fk FOREIGN KEY (
        release_tag,
        target_act_id,
        target_provision_id
    ) REFERENCES provisions (release_tag, act_id, provision_id),
    CONSTRAINT citations_record_is_object
        CHECK (jsonb_typeof(canonical_record) = 'object'),
    CONSTRAINT citations_record_type_matches CHECK (
        canonical_record ->> 'record_type' IS NOT DISTINCT FROM 'citation'
    ),
    CONSTRAINT citations_record_id_matches CHECK (
        canonical_record ->> 'citation_id' IS NOT DISTINCT FROM citation_id
    ),
    CONSTRAINT citations_record_schema_matches CHECK (
        canonical_record ->> 'schema_version' IS NOT DISTINCT FROM schema_version
    ),
    CONSTRAINT citations_record_source_provision_matches CHECK (
        canonical_record ->> 'source_provision_id'
            IS NOT DISTINCT FROM source_provision_id
    ),
    CONSTRAINT citations_record_source_block_matches CHECK (
        canonical_record ->> 'source_block_id'
            IS NOT DISTINCT FROM source_block_id
    ),
    CONSTRAINT citations_record_target_act_matches CHECK (
        canonical_record #>> '{target,act_id}'
            IS NOT DISTINCT FROM target_act_id
    ),
    CONSTRAINT citations_record_target_provision_matches CHECK (
        canonical_record #>> '{target,provision_id}'
            IS NOT DISTINCT FROM target_provision_id
    )
);

CREATE INDEX citations_source_idx
    ON citations (release_tag, source_provision_id, citation_id);
CREATE INDEX citations_target_idx
    ON citations (release_tag, target_act_id, target_provision_id);

COMMIT;
