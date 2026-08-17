-- Schema integration test for 001_projection.sql.
-- Run in a disposable PostgreSQL 17 database after applying the migration.

\set ON_ERROR_STOP on

BEGIN;

INSERT INTO corpus_releases (
    release_tag,
    commit_sha,
    canonical_schema_versions,
    import_state
) VALUES
    (
        'corpus-v0.1.0',
        repeat('a', 40),
        ARRAY['0.1.0'],
        'ready'
    ),
    (
        'corpus-v0.2.0',
        repeat('b', 40),
        ARRAY['0.1.0'],
        'ready'
    );

INSERT INTO sources (
    release_tag,
    source_id,
    schema_version,
    document_title,
    document_publisher,
    language,
    source_class,
    canonical_record
)
SELECT
    release_tag,
    'sha256:' || repeat('c', 64),
    '0.1.0',
    'Test Gazette',
    'Test Publisher',
    'eng',
    'official_gazette',
    jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'source',
        'source_id', 'sha256:' || repeat('c', 64),
        'document_title', 'Test Gazette',
        'document_publisher', 'Test Publisher',
        'language', 'eng',
        'source_class', 'official_gazette'
    )
FROM (VALUES ('corpus-v0.1.0'), ('corpus-v0.2.0')) AS releases (release_tag);

INSERT INTO acts (
    release_tag,
    act_id,
    schema_version,
    jurisdiction,
    country_code,
    official_title,
    short_title,
    year,
    number,
    citation,
    text_kind,
    status,
    checked_through_date,
    title_keys,
    citation_key,
    source_ids,
    searchable_text,
    canonical_record
)
SELECT
    release_tag,
    'ng-federal-act-2023-1',
    '0.1.0',
    'ng-federal',
    'NG',
    'Privacy Test Act, 2023',
    'Privacy Test Act',
    2023,
    '1',
    'Act No. 1 of 2023',
    'as_enacted',
    'unknown',
    NULL,
    ARRAY['privacy test act, 2023', 'privacy test act'],
    'act no 1 of 2023',
    ARRAY['sha256:' || repeat('c', 64)],
    'Privacy Test Act 2023 Act No. 1 of 2023',
    jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'act',
        'act_id', 'ng-federal-act-2023-1',
        'jurisdiction', 'ng-federal',
        'country_code', 'NG',
        'titles', jsonb_build_object(
            'official', 'Privacy Test Act, 2023',
            'short', 'Privacy Test Act'
        ),
        'year', 2023,
        'number', '1',
        'citation', 'Act No. 1 of 2023',
        'status', 'unknown',
        'checked_through_date', NULL
    )
FROM (VALUES ('corpus-v0.1.0'), ('corpus-v0.2.0')) AS releases (release_tag);

INSERT INTO provisions (
    release_tag,
    provision_id,
    act_id,
    schema_version,
    parent_provision_id,
    sibling_order,
    sequence,
    depth,
    node_type,
    display_label,
    heading,
    text_fidelity,
    reference_key,
    source_ids,
    searchable_text,
    canonical_record
) VALUES (
    'corpus-v0.1.0',
    'ng-federal-act-2023-1:section-1',
    'ng-federal-act-2023-1',
    '0.1.0',
    NULL,
    1,
    1,
    0,
    'section',
    '1.',
    'Privacy protections',
    'single_reviewed',
    'section 1',
    ARRAY['sha256:' || repeat('c', 64)],
    'Privacy protections apply to personal information.',
    jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'provision',
        'provision_id', 'ng-federal-act-2023-1:section-1',
        'parent_provision_id', NULL,
        'order', 1,
        'node_type', 'section',
        'display_label', '1.',
        'heading', 'Privacy protections',
        'text_fidelity', 'single_reviewed'
    )
), (
    'corpus-v0.2.0',
    'ng-federal-act-2023-1:section-2',
    'ng-federal-act-2023-1',
    '0.1.0',
    NULL,
    1,
    1,
    0,
    'section',
    '2.',
    'Later protections',
    'single_reviewed',
    'section 2',
    ARRAY['sha256:' || repeat('c', 64)],
    'Later privacy protections.',
    jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'provision',
        'provision_id', 'ng-federal-act-2023-1:section-2',
        'parent_provision_id', NULL,
        'order', 1,
        'node_type', 'section',
        'display_label', '2.',
        'heading', 'Later protections',
        'text_fidelity', 'single_reviewed'
    )
);

INSERT INTO citations (
    release_tag,
    citation_id,
    schema_version,
    source_provision_id,
    source_block_id,
    target_act_id,
    target_provision_id,
    canonical_record
) VALUES (
    'corpus-v0.1.0',
    'citation:ng-federal-act-2023-1:000001',
    '0.1.0',
    'ng-federal-act-2023-1:section-1',
    'block-1',
    'ng-federal-act-2023-1',
    NULL,
    jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'citation',
        'citation_id', 'citation:ng-federal-act-2023-1:000001',
        'source_provision_id', 'ng-federal-act-2023-1:section-1',
        'source_block_id', 'block-1',
        'target', jsonb_build_object(
            'act_id', 'ng-federal-act-2023-1',
            'provision_id', NULL
        )
    )
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM acts
        WHERE release_tag = 'corpus-v0.1.0'
          AND search_vector @@ plainto_tsquery('english', 'privacy')
    ) THEN
        RAISE EXCEPTION 'Act search vector did not index searchable_text';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM provisions
        WHERE release_tag = 'corpus-v0.1.0'
          AND search_vector @@ plainto_tsquery('english', 'protection')
    ) THEN
        RAISE EXCEPTION 'Provision search vector did not index searchable_text';
    END IF;

    IF (
        SELECT canonical_record
        FROM acts
        WHERE release_tag = 'corpus-v0.1.0'
          AND act_id = 'ng-federal-act-2023-1'
    ) <> jsonb_build_object(
        'schema_version', '0.1.0',
        'record_type', 'act',
        'act_id', 'ng-federal-act-2023-1',
        'jurisdiction', 'ng-federal',
        'country_code', 'NG',
        'titles', jsonb_build_object(
            'official', 'Privacy Test Act, 2023',
            'short', 'Privacy Test Act'
        ),
        'year', 2023,
        'number', '1',
        'citation', 'Act No. 1 of 2023',
        'status', 'unknown',
        'checked_through_date', NULL
    ) THEN
        RAISE EXCEPTION 'Canonical Act JSON changed during storage';
    END IF;

    IF to_regclass('acts_search_vector_idx') IS NULL
       OR to_regclass('acts_title_keys_idx') IS NULL
       OR to_regclass('provisions_search_vector_idx') IS NULL
       OR to_regclass('provisions_release_sequence_unique') IS NULL
       OR to_regclass('provisions_sibling_order_unique') IS NULL THEN
        RAISE EXCEPTION 'Required projection index is missing';
    END IF;
END
$$;

DO $$
DECLARE
    actual_constraint text;
BEGIN
    BEGIN
        INSERT INTO provisions (
            release_tag,
            provision_id,
            act_id,
            schema_version,
            parent_provision_id,
            sibling_order,
            sequence,
            depth,
            node_type,
            display_label,
            heading,
            text_fidelity,
            reference_key,
            source_ids,
            searchable_text,
            canonical_record
        ) VALUES (
            'corpus-v0.1.0',
            'ng-federal-act-2023-1:section-3',
            'ng-federal-act-2023-1',
            '0.1.0',
            NULL,
            2,
            1,
            0,
            'section',
            '3.',
            NULL,
            'single_reviewed',
            'section 3',
            ARRAY['sha256:' || repeat('c', 64)],
            'Duplicate sequence.',
            jsonb_build_object(
                'schema_version', '0.1.0',
                'record_type', 'provision',
                'provision_id', 'ng-federal-act-2023-1:section-3',
                'parent_provision_id', NULL,
                'order', 2,
                'node_type', 'section',
                'display_label', '3.',
                'heading', NULL,
                'text_fidelity', 'single_reviewed'
            )
        );
        RAISE EXCEPTION 'Duplicate Provision sequence was accepted';
    EXCEPTION
        WHEN unique_violation THEN
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            IF actual_constraint <> 'provisions_release_sequence_unique' THEN
                RAISE EXCEPTION 'Wrong sequence constraint failed: %',
                    actual_constraint;
            END IF;
    END;
END
$$;

DO $$
DECLARE
    actual_constraint text;
BEGIN
    BEGIN
        INSERT INTO provisions (
            release_tag,
            provision_id,
            act_id,
            schema_version,
            parent_provision_id,
            sibling_order,
            sequence,
            depth,
            node_type,
            display_label,
            heading,
            text_fidelity,
            reference_key,
            source_ids,
            searchable_text,
            canonical_record
        ) VALUES (
            'corpus-v0.1.0',
            'ng-federal-act-2023-1:section-4',
            'ng-federal-act-2023-1',
            '0.1.0',
            NULL,
            1,
            2,
            0,
            'section',
            '4.',
            NULL,
            'single_reviewed',
            'section 4',
            ARRAY['sha256:' || repeat('c', 64)],
            'Duplicate root order.',
            jsonb_build_object(
                'schema_version', '0.1.0',
                'record_type', 'provision',
                'provision_id', 'ng-federal-act-2023-1:section-4',
                'parent_provision_id', NULL,
                'order', 1,
                'node_type', 'section',
                'display_label', '4.',
                'heading', NULL,
                'text_fidelity', 'single_reviewed'
            )
        );
        RAISE EXCEPTION 'Duplicate root sibling order was accepted';
    EXCEPTION
        WHEN unique_violation THEN
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            IF actual_constraint <> 'provisions_sibling_order_unique' THEN
                RAISE EXCEPTION 'Wrong sibling constraint failed: %',
                    actual_constraint;
            END IF;
    END;
END
$$;

DO $$
DECLARE
    actual_constraint text;
BEGIN
    BEGIN
        INSERT INTO provisions (
            release_tag,
            provision_id,
            act_id,
            schema_version,
            parent_provision_id,
            sibling_order,
            sequence,
            depth,
            node_type,
            display_label,
            heading,
            text_fidelity,
            reference_key,
            source_ids,
            searchable_text,
            canonical_record
        ) VALUES (
            'corpus-v0.2.0',
            'ng-federal-act-2023-1:section-1.subsection-1',
            'ng-federal-act-2023-1',
            '0.1.0',
            'ng-federal-act-2023-1:section-1',
            1,
            2,
            1,
            'subsection',
            '(1)',
            NULL,
            'single_reviewed',
            'section 1 subsection 1',
            ARRAY['sha256:' || repeat('c', 64)],
            'Cross-release parent.',
            jsonb_build_object(
                'schema_version', '0.1.0',
                'record_type', 'provision',
                'provision_id',
                    'ng-federal-act-2023-1:section-1.subsection-1',
                'parent_provision_id', 'ng-federal-act-2023-1:section-1',
                'order', 1,
                'node_type', 'subsection',
                'display_label', '(1)',
                'heading', NULL,
                'text_fidelity', 'single_reviewed'
            )
        );
        RAISE EXCEPTION 'Cross-release Provision parent was accepted';
    EXCEPTION
        WHEN foreign_key_violation THEN
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            IF actual_constraint <> 'provisions_parent_fk' THEN
                RAISE EXCEPTION 'Wrong parent constraint failed: %',
                    actual_constraint;
            END IF;
    END;
END
$$;

DO $$
DECLARE
    actual_constraint text;
BEGIN
    BEGIN
        INSERT INTO citations (
            release_tag,
            citation_id,
            schema_version,
            source_provision_id,
            source_block_id,
            target_act_id,
            target_provision_id,
            canonical_record
        ) VALUES (
            'corpus-v0.1.0',
            'citation:ng-federal-act-2023-1:000002',
            '0.1.0',
            'ng-federal-act-2023-1:section-1',
            'block-1',
            'ng-federal-act-2023-1',
            'ng-federal-act-2023-1:section-2',
            jsonb_build_object(
                'schema_version', '0.1.0',
                'record_type', 'citation',
                'citation_id', 'citation:ng-federal-act-2023-1:000002',
                'source_provision_id', 'ng-federal-act-2023-1:section-1',
                'source_block_id', 'block-1',
                'target', jsonb_build_object(
                    'act_id', 'ng-federal-act-2023-1',
                    'provision_id', 'ng-federal-act-2023-1:section-2'
                )
            )
        );
        RAISE EXCEPTION 'Cross-release Citation target was accepted';
    EXCEPTION
        WHEN foreign_key_violation THEN
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            IF actual_constraint <> 'citations_target_provision_fk' THEN
                RAISE EXCEPTION 'Wrong Citation target constraint failed: %',
                    actual_constraint;
            END IF;
    END;
END
$$;

DO $$
DECLARE
    actual_constraint text;
BEGIN
    BEGIN
        UPDATE projection_state
        SET active_release_tag = 'corpus-v0.1.0',
            previous_release_tag = 'corpus-v0.1.0',
            activated_at = CURRENT_TIMESTAMP
        WHERE singleton;
        RAISE EXCEPTION 'Identical active and previous releases were accepted';
    EXCEPTION
        WHEN check_violation THEN
            GET STACKED DIAGNOSTICS actual_constraint = CONSTRAINT_NAME;
            IF actual_constraint <> 'projection_state_distinct_releases' THEN
                RAISE EXCEPTION 'Wrong projection-state constraint failed: %',
                    actual_constraint;
            END IF;
    END;
END
$$;

UPDATE projection_state
SET active_release_tag = 'corpus-v0.2.0',
    previous_release_tag = 'corpus-v0.1.0',
    activated_at = CURRENT_TIMESTAMP
WHERE singleton;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM projection_state
        WHERE singleton
          AND active_release_tag = 'corpus-v0.2.0'
          AND previous_release_tag = 'corpus-v0.1.0'
    ) THEN
        RAISE EXCEPTION 'Valid release activation was not stored';
    END IF;
END
$$;

ROLLBACK;
