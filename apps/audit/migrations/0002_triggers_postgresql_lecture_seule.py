"""
FR-AUD-02, défense en profondeur : en plus du blocage au niveau ORM
(JournalAudit.save/delete), un accès direct à PostgreSQL (hors Django) est
également bloqué par des triggers BEFORE UPDATE/DELETE. Sans effet sur
sqlite (utilisé en tests/dev) : la garantie ORM y est jugée suffisante,
et sqlite ne supporte pas cette syntaxe de trigger PL/pgSQL.
"""
from django.db import migrations

SQL_CREER_TRIGGERS = """
CREATE OR REPLACE FUNCTION fn_interdire_modif_audit() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Le journal d''audit est en lecture seule : UPDATE/DELETE interdits (FR-AUD-02)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_readonly_update
    BEFORE UPDATE ON journal_audit
    FOR EACH ROW EXECUTE FUNCTION fn_interdire_modif_audit();

CREATE TRIGGER trg_audit_readonly_delete
    BEFORE DELETE ON journal_audit
    FOR EACH ROW EXECUTE FUNCTION fn_interdire_modif_audit();
"""

SQL_RETIRER_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_audit_readonly_update ON journal_audit;
DROP TRIGGER IF EXISTS trg_audit_readonly_delete ON journal_audit;
DROP FUNCTION IF EXISTS fn_interdire_modif_audit();
"""


def appliquer(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_CREER_TRIGGERS)


def retirer(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL_RETIRER_TRIGGERS)


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [migrations.RunPython(appliquer, retirer)]
