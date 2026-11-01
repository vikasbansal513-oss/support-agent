"""billing: Plivo CDR + number rental, STT/TTS per-call usage, GCP app-level metering +
BigQuery billing-export import + reconciliation + unallocated costs, invoice payment ledger
(partial payments) and invoice cancellation. Loosens invoice_final_lock so paid/cancelled
tracking columns can still change after an invoice is finalized."""
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _admin_only(table: str) -> list[str]:
    return [
        f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY',
        f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY',
        f'CREATE POLICY super_admin_only ON "{table}"'
        " USING (app_is_super_admin()) WITH CHECK (app_is_super_admin())",
    ]


_NEW_FINAL_LOCK = """CREATE OR REPLACE FUNCTION invoice_final_lock() RETURNS trigger
LANGUAGE plpgsql AS
$$ BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.status = 'final' THEN
      RAISE EXCEPTION 'final invoice cannot be deleted';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status = 'final' AND (
    NEW.status IS DISTINCT FROM OLD.status OR
    NEW.doc_type IS DISTINCT FROM OLD.doc_type OR
    NEW.invoice_no IS DISTINCT FROM OLD.invoice_no OR
    NEW.invoice_date IS DISTINCT FROM OLD.invoice_date OR
    NEW.due_date IS DISTINCT FROM OLD.due_date OR
    NEW.usd_inr IS DISTINCT FROM OLD.usd_inr OR
    NEW.cloudrun_basis IS DISTINCT FROM OLD.cloudrun_basis OR
    NEW.llm_paise IS DISTINCT FROM OLD.llm_paise OR
    NEW.cloudrun_paise IS DISTINCT FROM OLD.cloudrun_paise OR
    NEW.telephony_paise IS DISTINCT FROM OLD.telephony_paise OR
    NEW.platform_fee_paise IS DISTINCT FROM OLD.platform_fee_paise OR
    NEW.subtotal_paise IS DISTINCT FROM OLD.subtotal_paise OR
    NEW.gst_rate_percent IS DISTINCT FROM OLD.gst_rate_percent OR
    NEW.gst_kind IS DISTINCT FROM OLD.gst_kind OR
    NEW.cgst_paise IS DISTINCT FROM OLD.cgst_paise OR
    NEW.sgst_paise IS DISTINCT FROM OLD.sgst_paise OR
    NEW.igst_paise IS DISTINCT FROM OLD.igst_paise OR
    NEW.total_paise IS DISTINCT FROM OLD.total_paise OR
    NEW.seller IS DISTINCT FROM OLD.seller OR
    NEW.buyer IS DISTINCT FROM OLD.buyer OR
    NEW.generated_at IS DISTINCT FROM OLD.generated_at OR
    NEW.finalized_at IS DISTINCT FROM OLD.finalized_at OR
    NEW.finalized_by IS DISTINCT FROM OLD.finalized_by OR
    NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
    NEW.period_start IS DISTINCT FROM OLD.period_start
  ) THEN
    RAISE EXCEPTION 'final invoice cannot be changed except payment/cancellation tracking';
  END IF;
  RETURN NEW;
END $$"""

_OLD_FINAL_LOCK = """CREATE OR REPLACE FUNCTION invoice_final_lock() RETURNS trigger
LANGUAGE plpgsql AS
$$ BEGIN
  IF OLD.status = 'final' THEN
    RAISE EXCEPTION 'final invoice cannot be changed or deleted';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

_FIND_CHECK = """DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conname FROM pg_constraint
    WHERE contype = 'c' AND conrelid = '{table}'::regclass
      AND pg_get_constraintdef(oid) ~ '{marker}'
  LOOP
    EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', r.conname);
  END LOOP;
END $$"""

UP = [
    # --- invoices: cancellation tracking + loosened trigger so paid/cancelled can be set
    # on a finalized invoice without tripping the immutability trigger ---
    "ALTER TABLE invoices ADD COLUMN cancelled_at timestamptz",
    "ALTER TABLE invoices ADD COLUMN cancelled_by uuid",
    "ALTER TABLE invoices ADD COLUMN cancelled_reason text NOT NULL DEFAULT ''"
    " CHECK (length(cancelled_reason) <= 300)",
    _NEW_FINAL_LOCK,
    # --- invoices: speech_paise (STT/TTS) column, widen subtotal check + line-kind check ---
    "ALTER TABLE invoices ADD COLUMN speech_paise bigint NOT NULL DEFAULT 0"
    " CHECK (speech_paise >= 0)",
    _FIND_CHECK.format(table="invoices", marker="subtotal_paise = "),
    "ALTER TABLE invoices ADD CONSTRAINT invoices_subtotal_check"
    " CHECK (subtotal_paise = llm_paise + cloudrun_paise + telephony_paise + speech_paise"
    " + platform_fee_paise)",
    _FIND_CHECK.format(table="invoice_lines", marker="kind = ANY"),
    "ALTER TABLE invoice_lines ADD CONSTRAINT invoice_lines_kind_check"
    " CHECK (kind IN ('llm','cloudrun','telephony','speech','platform_fee'))",
    # --- invoice_payments: partial-payment ledger, separate from the immutable invoice row ---
    """CREATE TABLE invoice_payments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  invoice_id uuid NOT NULL,
  amount_paise bigint NOT NULL CHECK (amount_paise > 0),
  paid_on date NOT NULL,
  method text NOT NULL DEFAULT '' CHECK (length(method) <= 40),
  note text NOT NULL DEFAULT '' CHECK (length(note) <= 300),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, invoice_id) REFERENCES invoices (tenant_id, id) ON DELETE CASCADE
)""",
    "CREATE INDEX ix_invoice_payments_invoice ON invoice_payments (invoice_id)",
    *_admin_only("invoice_payments"),
    # --- plivo_number_assignments: which client a Plivo number was assigned to, and when ---
    """CREATE TABLE plivo_number_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plivo_number text NOT NULL CHECK (length(plivo_number) BETWEEN 1 AND 20),
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  assigned_from date NOT NULL,
  assigned_to date,
  monthly_rate_paise bigint NOT NULL DEFAULT 0 CHECK (monthly_rate_paise >= 0),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (assigned_to IS NULL OR assigned_to >= assigned_from)
)""",
    "CREATE INDEX ix_plivo_assign_number ON plivo_number_assignments (plivo_number, assigned_from)",
    "CREATE INDEX ix_plivo_assign_tenant ON plivo_number_assignments (tenant_id)",
    *_admin_only("plivo_number_assignments"),
    # --- plivo_call_records: CDR, idempotent on call_uuid (webhook may resend) ---
    """CREATE TABLE plivo_call_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  call_uuid text NOT NULL,
  tenant_id uuid REFERENCES tenants(id),
  direction text NOT NULL DEFAULT 'inbound' CHECK (direction IN ('inbound', 'outbound')),
  from_number text NOT NULL DEFAULT '' CHECK (length(from_number) <= 20),
  to_number text NOT NULL DEFAULT '' CHECK (length(to_number) <= 20),
  duration_seconds integer NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
  billed_seconds integer NOT NULL DEFAULT 0 CHECK (billed_seconds >= 0),
  call_status text NOT NULL DEFAULT 'completed' CHECK (length(call_status) <= 30),
  provider_charge_inr numeric(10,4),
  computed_cost_paise bigint NOT NULL DEFAULT 0 CHECK (computed_cost_paise >= 0),
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  voice_call_id uuid,
  raw jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (call_uuid)
)""",
    "CREATE INDEX ix_plivo_calls_period ON plivo_call_records (billing_period, tenant_id)",
    *_admin_only("plivo_call_records"),
    # --- speech_usage_records: per-call STT/TTS usage, idempotent on event_key ---
    """CREATE TABLE speech_usage_records (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_key text NOT NULL,
  tenant_id uuid NOT NULL REFERENCES tenants(id),
  voice_call_id uuid,
  provider text NOT NULL CHECK (provider IN ('sarvam_stt', 'google_stt', 'sarvam_tts', 'google_tts')),
  quantity numeric(18,4) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  unit text NOT NULL DEFAULT '' CHECK (length(unit) <= 40),
  rate_inr_snapshot numeric(18,6) NOT NULL DEFAULT 0 CHECK (rate_inr_snapshot >= 0),
  cost_paise bigint NOT NULL DEFAULT 0 CHECK (cost_paise >= 0),
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_key)
)""",
    "CREATE INDEX ix_speech_usage_period ON speech_usage_records (billing_period, tenant_id)",
    *_admin_only("speech_usage_records"),
    # --- gcp_usage_events: app-level SKU metering (tenant_id NULL = shared/unattributed) ---
    """CREATE TABLE gcp_usage_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_key text NOT NULL,
  tenant_id uuid REFERENCES tenants(id),
  category text NOT NULL REFERENCES billing_cost_categories(category),
  quantity numeric(18,6) NOT NULL DEFAULT 0 CHECK (quantity >= 0),
  unit text NOT NULL DEFAULT '' CHECK (length(unit) <= 40),
  usd_rate_snapshot numeric(18,6) NOT NULL DEFAULT 0 CHECK (usd_rate_snapshot >= 0),
  cost_usd numeric(18,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  event_source text NOT NULL DEFAULT 'app_metering' CHECK (length(event_source) <= 40),
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event_key)
)""",
    "CREATE INDEX ix_gcp_usage_period ON gcp_usage_events (billing_period, category)",
    *_admin_only("gcp_usage_events"),
    # --- gcp_billing_import_rows: raw rows from Google Cloud Billing Export (BigQuery) ---
    """CREATE TABLE gcp_billing_import_rows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  import_batch_id uuid NOT NULL,
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  billing_account text NOT NULL DEFAULT '' CHECK (length(billing_account) <= 100),
  service text NOT NULL DEFAULT '' CHECK (length(service) <= 150),
  sku text NOT NULL DEFAULT '' CHECK (length(sku) <= 150),
  usage_quantity numeric(18,6) NOT NULL DEFAULT 0,
  usage_unit text NOT NULL DEFAULT '' CHECK (length(usage_unit) <= 40),
  cost_usd numeric(18,6) NOT NULL DEFAULT 0,
  credits_usd numeric(18,6) NOT NULL DEFAULT 0,
  currency text NOT NULL DEFAULT 'USD' CHECK (length(currency) <= 10),
  resource_name text NOT NULL DEFAULT '' CHECK (length(resource_name) <= 300),
  row_key text NOT NULL,
  imported_by uuid,
  imported_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (row_key)
)""",
    "CREATE INDEX ix_gcp_import_period ON gcp_billing_import_rows (billing_period, service, sku)",
    *_admin_only("gcp_billing_import_rows"),
    # --- gcp_reconciliation_runs: one row per reconciliation run for a period ---
    """CREATE TABLE gcp_reconciliation_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  imported_total_usd numeric(18,6) NOT NULL DEFAULT 0,
  metered_total_usd numeric(18,6) NOT NULL DEFAULT 0,
  allocated_usd numeric(18,6) NOT NULL DEFAULT 0,
  unallocated_usd numeric(18,6) NOT NULL DEFAULT 0,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_gcp_recon_period ON gcp_reconciliation_runs (billing_period DESC)",
    *_admin_only("gcp_reconciliation_runs"),
    # --- gcp_unallocated_costs: costs a reconciliation run could not attribute to any client ---
    """CREATE TABLE gcp_unallocated_costs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  billing_period date NOT NULL CHECK (EXTRACT(day FROM billing_period) = 1),
  category text NOT NULL DEFAULT '' CHECK (length(category) <= 150),
  amount_usd numeric(18,6) NOT NULL DEFAULT 0,
  reason text NOT NULL DEFAULT '' CHECK (length(reason) <= 300),
  resolved boolean NOT NULL DEFAULT false,
  resolved_by uuid,
  resolved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
)""",
    "CREATE INDEX ix_gcp_unalloc_period ON gcp_unallocated_costs (billing_period, resolved)",
    *_admin_only("gcp_unallocated_costs"),
]

DOWN = [
    _FIND_CHECK.format(table="invoice_lines", marker="kind = ANY"),
    "ALTER TABLE invoice_lines ADD CONSTRAINT invoice_lines_kind_check"
    " CHECK (kind IN ('llm','cloudrun','telephony','platform_fee'))",
    _FIND_CHECK.format(table="invoices", marker="subtotal_paise = "),
    "ALTER TABLE invoices ADD CONSTRAINT invoices_subtotal_check"
    " CHECK (subtotal_paise = llm_paise + cloudrun_paise + telephony_paise + platform_fee_paise)",
    "ALTER TABLE invoices DROP COLUMN speech_paise",
    "DROP TABLE gcp_unallocated_costs",
    "DROP TABLE gcp_reconciliation_runs",
    "DROP TABLE gcp_billing_import_rows",
    "DROP TABLE gcp_usage_events",
    "DROP TABLE speech_usage_records",
    "DROP TABLE plivo_call_records",
    "DROP TABLE plivo_number_assignments",
    "DROP TABLE invoice_payments",
    _OLD_FINAL_LOCK,
    "ALTER TABLE invoices DROP COLUMN cancelled_reason",
    "ALTER TABLE invoices DROP COLUMN cancelled_by",
    "ALTER TABLE invoices DROP COLUMN cancelled_at",
]


def upgrade() -> None:
    for statement in UP:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWN:
        op.execute(statement)
