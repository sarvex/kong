return {
  postgres = {
    up = [[
      DROP TRIGGER IF EXISTS "acme_storage_ttl_trigger" ON "acme_storage";

      DO $$
      BEGIN
        CREATE TRIGGER "acme_storage_ttl_trigger"
        AFTER INSERT ON "acme_storage"
        FOR EACH STATEMENT
        EXECUTE PROCEDURE batch_delete_expired_rows("ttl");
      EXCEPTION WHEN UNDEFINED_COLUMN OR UNDEFINED_TABLE THEN
        -- Do nothing, accept existing state
      END$$;
    ]],
  },
  cassandra = {
    up = "",
  }
}
