import os
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://autoheal:autoheal@localhost:5432/autoheal",
)


def get_connection():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=5,
    )


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id BIGSERIAL PRIMARY KEY,
                fingerprint TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                status TEXT NOT NULL DEFAULT 'open',
                source TEXT NOT NULL DEFAULT 'alertmanager',
                labels JSONB NOT NULL DEFAULT '{}'::jsonb,
                annotations JSONB NOT NULL DEFAULT '{}'::jsonb,
                starts_at TIMESTAMPTZ,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_events (
                id BIGSERIAL PRIMARY KEY,
                incident_id BIGINT NOT NULL
                    REFERENCES incidents(id),
                event_type TEXT NOT NULL,
                details JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rca_analyses (
                id BIGSERIAL PRIMARY KEY,
                incident_id BIGINT NOT NULL
                    REFERENCES incidents(id),
                root_cause TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL
                    CHECK (confidence >= 0 AND confidence <= 1),
                supporting_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                evidence_gaps JSONB NOT NULL DEFAULT '[]'::jsonb,
                proposed_action TEXT NOT NULL
                    CHECK (proposed_action IN (
                        'set_fault_normal',
                        'reset_injected_fault',
                        'rollback_release',
                        'enable_payment_fallback',
                        'no_action'
                    )),
                action_reason TEXT NOT NULL,
                requires_approval BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS remediation_actions (
                id BIGSERIAL PRIMARY KEY,
                incident_id BIGINT NOT NULL
                    REFERENCES incidents(id),
                rca_analysis_id BIGINT NOT NULL
                    REFERENCES rca_analyses(id),
                action TEXT NOT NULL
                    CHECK (action IN (
                        'set_fault_normal',
                        'reset_injected_fault',
                        'rollback_release',
                        'enable_payment_fallback'
                    )),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending', 'executing', 'succeeded',
                        'failed', 'rejected'
                    )),
                requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                approved_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                verification JSONB NOT NULL DEFAULT '{}'::jsonb,
                error TEXT
            )
        """)

        # Migrate existing databases to support the remediation registry.
        conn.execute("""
            ALTER TABLE rca_analyses
            DROP CONSTRAINT IF EXISTS rca_analyses_proposed_action_check
        """)

        conn.execute("""
            ALTER TABLE rca_analyses
            ADD CONSTRAINT rca_analyses_proposed_action_check
            CHECK (proposed_action IN (
                'set_fault_normal',
                'restart_service',
                'reset_injected_fault',
                'rollback_release',
                'enable_payment_fallback',
                'no_action'
            ))
        """)

        conn.execute("""
            ALTER TABLE remediation_actions
            DROP CONSTRAINT IF EXISTS remediation_actions_action_check
        """)

        conn.execute("""
            ALTER TABLE remediation_actions
            ADD CONSTRAINT remediation_actions_action_check
            CHECK (action IN (
                'set_fault_normal',
                'restart_service',
                'reset_injected_fault',
                'rollback_release',
                'enable_payment_fallback'
            ))
        """)
