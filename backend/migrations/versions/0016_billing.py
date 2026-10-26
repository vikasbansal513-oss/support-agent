"""billing: settings, LLM rates, periods, invoice counters, brand billing profiles, invoices"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

GSTIN = "'^([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])?$'"
STATE = "'^([0-9]{2})?$'"


def _admin_only(table: str) -> list[str]:
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY super_admin_only ON "{table}"'
        " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
    ]


UP = [
    f"""CREATE TABLE billing_settings (
  id smallint PRIMARY KEY CHECK (id = 1),
  seller_name text NOT NULL DEFAULT '' CHECK (length(seller_name) <= 200),
  seller_address text NOT NULL DEFAULT '' CHECK (length(seller_address) <= 500),
  seller_state_code text NOT NULL DEFAULT '' CHECK (seller_state_code ~ {STATE}),
  seller_gstin text NOT NULL DEFAULT '' CHECK (seller_gstin ~ {GSTIN}),
  seller_email text NOT NULL DEFAULT '' CHECK (length(seller_email) <= 254),
  seller_phone text NOT NULL DEFAULT '' CHECK (length(seller_phone) <= 30),
  payment_details text NOT NULL DEFAULT '' CHECK (length(payment_details) <= 500),
  sac_code text NOT NULL DEFAULT '' CHECK (sac_code ~ '^[0-9]{{0,8}}$'),
  invoice_prefix text NOT NULL DEFAULT 'INV' CHECK (invoice_prefix ~ '^[A-Z0-9]{{1,3}}$'),
  gst_enabled boolean NOT NULL DEFAULT true,
  gst_rate_percent numeric(5,2) NOT NULL DEFAULT 18.00
    CHECK (gst_rate_percent >= 0 AND gst_rate_percent <= 100),
  cloudrun_vcpu numeric(8,3) NOT NULL DEFAULT 0 CHECK (cloudrun_vcpu >= 0),
  cloudrun_gib numeric(8,3) NOT NULL DEFAULT 0 CHECK (cloudrun_gib >= 0),
  cloudrun_usd_per_vcpu_second numeric(18,10) NOT NULL DEFAULT 0
    CHECK (cloudrun_usd_per_vcpu_second >= 0),
  cloudrun_usd_per_gib_second numeric(18,10) NOT NULL DEFAULT 0
    CHECK (cloudrun_usd_per_gib_second >= 0),
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *_admin_only("billing_settings"),
    """CREATE TABLE billing_llm_rates (
  provider text NOT NULL CHECK (provider ~ '^[a-z0-9_]{1,40}$'),
  model text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
  input_usd_per_million numeric(18,6) NOT NULL CHECK (input_usd_per_million >= 0),
  output_usd_per_million numeric(18,6) NOT NULL CHECK (output_usd_per_million >= 0),
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, model)
)""",
    *_admin_only("billing_llm_rates"),
    """CREATE TABLE billing_periods (
  period_start date PRIMARY KEY CHECK (EXTRACT(day FROM period_start) = 1),
  usd_inr numeric(12,4) CHECK (usd_inr IS NULL OR usd_inr > 0),
  cloudrun_actual_paise bigint CHECK (cloudrun_actual_paise IS NULL OR cloudrun_actual_paise >= 0),
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *_admin_only("billing_periods"),
    """CREATE TABLE invoice_counters (
  fy text PRIMARY KEY CHECK (fy ~ '^[0-9]{4}-[0-9]{2}$'),
  last_no integer NOT NULL CHECK (last_no >= 0)
)""",
    *_admin_only("invoice_counters"),
    f"""CREATE TABLE tenant_billing_profiles (
  tenant_id uuid PRIMARY KEY DEFAULT app_current_tenant() REFERENCES tenants(id),
  legal_name text NOT NULL DEFAULT '' CHECK (length(legal_name) <= 200),
  address text NOT NULL DEFAULT '' CHECK (length(address) <= 500),
  state_code text NOT NULL DEFAULT '' CHECK (state_code ~ {STATE}),
  gstin text NOT NULL DEFAULT '' CHECK (gstin ~ {GSTIN}),
  email text NOT NULL DEFAULT '' CHECK (length(email) <= 254),
  default_platform_fee_paise bigint NOT NULL DEFAULT 0 CHECK (default_platform_fee_paise >= 0),
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
)""",
    *_admin_only("tenant_billing_profiles"),
    """CREATE TABLE invoices (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  period_start date NOT NULL CHECK (EXTRACT(day FROM period_start) = 1),
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','final')),
  doc_type text NOT NULL DEFAULT 'proforma' CHECK (doc_type IN ('tax_invoice','proforma')),
  invoice_no text CHECK (length(invoice_no) <= 16),
  invoice_date date,
  usd_inr numeric(12,4) NOT NULL CHECK (usd_inr > 0),
  cloudrun_basis text NOT NULL CHECK (cloudrun_basis IN ('estimate','actual')),
  llm_paise bigint NOT NULL DEFAULT 0 CHECK (llm_paise >= 0),
  cloudrun_paise bigint NOT NULL DEFAULT 0 CHECK (cloudrun_paise >= 0),
  platform_fee_paise bigint NOT NULL DEFAULT 0 CHECK (platform_fee_paise >= 0),
  subtotal_paise bigint NOT NULL DEFAULT 0,
  gst_rate_percent numeric(5,2) NOT NULL DEFAULT 0,
  gst_kind text NOT NULL DEFAULT 'none' CHECK (gst_kind IN ('none','intra','inter')),
  cgst_paise bigint NOT NULL DEFAULT 0 CHECK (cgst_paise >= 0),
  sgst_paise bigint NOT NULL DEFAULT 0 CHECK (sgst_paise >= 0),
  igst_paise bigint NOT NULL DEFAULT 0 CHECK (igst_paise >= 0),
  total_paise bigint NOT NULL DEFAULT 0,
  seller jsonb,
  buyer jsonb,
  generated_at timestamptz NOT NULL DEFAULT now(),
  finalized_at timestamptz,
  finalized_by uuid,
  UNIQUE (tenant_id, period_start),
  UNIQUE (tenant_id, id),
  CHECK (subtotal_paise = llm_paise + cloudrun_paise + platform_fee_paise),
  CHECK (total_paise = subtotal_paise + cgst_paise + sgst_paise + igst_paise),
  CHECK (status = 'draft' OR (invoice_no IS NOT NULL AND invoice_date IS NOT NULL
    AND seller IS NOT NULL AND buyer IS NOT NULL AND finalized_at IS NOT NULL))
)""",
    "CREATE UNIQUE INDEX ux_invoices_no ON invoices (invoice_no) WHERE invoice_no IS NOT NULL",
    "CREATE INDEX ix_invoices_period ON invoices (period_start, tenant_id)",
    *_admin_only("invoices"),
    """CREATE TABLE invoice_lines (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL DEFAULT app_current_tenant() REFERENCES tenants(id),
  invoice_id uuid NOT NULL,
  position integer NOT NULL CHECK (position >= 1),
  kind text NOT NULL CHECK (kind IN ('llm','cloudrun','platform_fee')),
  description text NOT NULL CHECK (length(description) BETWEEN 1 AND 300),
  quantity numeric(18,4) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  unit text NOT NULL DEFAULT '' CHECK (length(unit) <= 20),
  amount_paise bigint NOT NULL CHECK (amount_paise >= 0),
  detail jsonb NOT NULL DEFAULT '{}',
  UNIQUE (invoice_id, position),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE CASCADE
)""",
    *_admin_only("invoice_lines"),
    """CREATE FUNCTION invoice_final_lock() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN
  IF OLD.status = 'final' THEN
    RAISE EXCEPTION 'final invoice cannot be changed or deleted';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$""",
    "CREATE TRIGGER invoices_final_lock BEFORE UPDATE OR DELETE ON invoices"
    " FOR EACH ROW EXECUTE FUNCTION invoice_final_lock()",
    """CREATE FUNCTION invoice_line_final_lock() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN
  IF EXISTS (SELECT 1 FROM invoices WHERE id = COALESCE(NEW.invoice_id, OLD.invoice_id)
             AND status = 'final') THEN
    RAISE EXCEPTION 'lines of a final invoice cannot be changed';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$""",
    "CREATE TRIGGER invoice_lines_final_lock BEFORE INSERT OR UPDATE OR DELETE ON invoice_lines"
    " FOR EACH ROW EXECUTE FUNCTION invoice_line_final_lock()",
]

DOWN = [
    "DROP TABLE invoice_lines",
    "DROP TABLE invoices",
    "DROP FUNCTION invoice_line_final_lock()",
    "DROP FUNCTION invoice_final_lock()",
    "DROP TABLE tenant_billing_profiles",
    "DROP TABLE invoice_counters",
    "DROP TABLE billing_periods",
    "DROP TABLE billing_llm_rates",
    "DROP TABLE billing_settings",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
