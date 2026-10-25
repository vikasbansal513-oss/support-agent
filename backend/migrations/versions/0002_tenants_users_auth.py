"""tenants, users, platform_admins, memberships, auth token tables, rate_limits, user_mfa"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UP = [
    """CREATE TABLE tenants (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,40}$'),
  status text NOT NULL DEFAULT 'pending_approval' CHECK (status IN ('pending_approval','active','paused','suspended')),
  agent_paused boolean NOT NULL DEFAULT false,
  mode text NOT NULL DEFAULT 'shadow' CHECK (mode IN ('shadow','draft','auto')),
  max_tool_risk text NOT NULL DEFAULT 'low' CHECK (max_tool_risk IN ('low')),
  plan text NOT NULL DEFAULT 'pilot',
  included_conversations integer NOT NULL DEFAULT 500 CHECK (included_conversations >= 0),
  usage_override_limit integer CHECK (usage_override_limit >= 0),
  approved_by uuid, approved_at timestamptz, notes text,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    """ALTER TABLE tenants ENABLE ROW LEVEL SECURITY""",
    """ALTER TABLE tenants FORCE ROW LEVEL SECURITY""",
    """CREATE POLICY tenants_rls ON tenants USING (id = app_current_tenant() OR app_is_super_admin())
  WITH CHECK (id = app_current_tenant() OR app_is_super_admin())""",
    """CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL CHECK (email = lower(email) AND length(email) <= 254),
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  password_hash text,
  email_verified_at timestamptz,
  failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
  locked_until timestamptz,
  is_active boolean NOT NULL DEFAULT true,
  password_changed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE UNIQUE INDEX ux_users_email ON users (email)""",
    """CREATE TABLE platform_admins (user_id uuid PRIMARY KEY REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE memberships (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  user_id uuid NOT NULL REFERENCES users(id),
  role text NOT NULL CHECK (role IN ('owner','agent')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id), UNIQUE (user_id)  -- V0: one user = one brand
)""",
    """ALTER TABLE memberships ENABLE ROW LEVEL SECURITY""",
    """ALTER TABLE memberships FORCE ROW LEVEL SECURITY""",
    """CREATE POLICY memberships_rls ON memberships
  USING (tenant_id = app_current_tenant() OR user_id = app_current_user() OR app_is_super_admin())
  WITH CHECK (tenant_id = app_current_tenant() OR app_is_super_admin())""",
    """CREATE TABLE email_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  purpose text NOT NULL CHECK (purpose IN ('verify','reset')),
  token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL, used_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE INDEX ix_email_tokens_user ON email_tokens (user_id, purpose, created_at DESC)""",
    """CREATE TABLE refresh_tokens (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id),
  realm text NOT NULL DEFAULT 'brand' CHECK (realm IN ('brand','admin')),
  family_id uuid NOT NULL, token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL,
  revoked_at timestamptz, replaced_by uuid, created_at timestamptz NOT NULL DEFAULT now()
)""",
    """CREATE INDEX ix_refresh_family ON refresh_tokens (family_id)""",
    """CREATE INDEX ix_refresh_user ON refresh_tokens (user_id)""",
    """CREATE TABLE rate_limits (
  bucket text NOT NULL, window_start timestamptz NOT NULL, hits integer NOT NULL CHECK (hits >= 0),
  PRIMARY KEY (bucket, window_start)
)""",
    """CREATE TABLE user_mfa (
  user_id uuid PRIMARY KEY REFERENCES users(id),
  secret_enc text NOT NULL, confirmed_at timestamptz, last_step bigint,
  recovery_hashes text[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
)""",
]

DOWN = [
    "DROP TABLE user_mfa",
    "DROP TABLE rate_limits",
    "DROP TABLE refresh_tokens",
    "DROP TABLE email_tokens",
    "DROP TABLE memberships",
    "DROP TABLE platform_admins",
    "DROP TABLE users",
    "DROP TABLE tenants",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
