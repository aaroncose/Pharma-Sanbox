-- Preparación de la base de datos. Se ejecuta UNA vez, con un superusuario.
--
--   psql -d pharma_sandbox -f scripts/bootstrap-db.sql
--
-- Todo lo demás (`make migrate`, `make seed`, la API) corre con roles sin
-- privilegios. Aislar aquí lo que exige superusuario deja explícito cuál es la
-- superficie privilegiada real del sistema: dos extensiones y dos roles.

-- ── Roles ────────────────────────────────────────────────────────────────────
-- `pharma_owner`  crea el esquema. Nunca atiende peticiones HTTP.
-- `pharma_app`    se conecta desde la API. Sin BYPASSRLS: es lo único que hace
--                 que Row-Level Security se aplique de verdad.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pharma_owner') THEN
        CREATE ROLE pharma_owner LOGIN PASSWORD 'pharma_owner_dev'
            NOSUPERUSER NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pharma_app') THEN
        CREATE ROLE pharma_app LOGIN PASSWORD 'pharma_app_dev'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END $$;

-- Se reafirma aunque el rol ya existiera: un rol que gane BYPASSRLS por
-- cualquier vía desactiva el aislamiento entre clientes sin romper nada visible.
ALTER ROLE pharma_app  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
ALTER ROLE pharma_owner NOSUPERUSER NOBYPASSRLS;

GRANT CONNECT ON DATABASE pharma_sandbox TO pharma_owner, pharma_app;

-- ── Extensiones ──────────────────────────────────────────────────────────────
-- Fuera de `public` a propósito. `make reset-db` hace `DROP SCHEMA public
-- CASCADE`, y si las extensiones vivieran ahí se destruirían en cada reset,
-- obligando a tener un superusuario a mano para reconstruirlas.

CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO pharma_owner, pharma_app;

CREATE EXTENSION IF NOT EXISTS vector   WITH SCHEMA extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

-- El tipo `vector` y `gen_random_uuid()` tienen que resolverse sin cualificar
-- en cada sentencia del esquema.
ALTER DATABASE pharma_sandbox SET search_path TO public, extensions;

-- ── Propiedad del esquema de trabajo ─────────────────────────────────────────

ALTER SCHEMA public OWNER TO pharma_owner;
GRANT USAGE ON SCHEMA public TO pharma_app;

-- El rol de la API no puede crear objetos, luego tampoco puede eliminar una
-- política RLS creando una tabla que la sustituya.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM pharma_app;

ALTER DEFAULT PRIVILEGES FOR ROLE pharma_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pharma_app;
ALTER DEFAULT PRIVILEGES FOR ROLE pharma_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO pharma_app;

-- ── Comprobación ─────────────────────────────────────────────────────────────

SELECT rolname,
       rolsuper     AS superusuario,
       rolbypassrls AS puede_saltarse_rls
  FROM pg_roles
 WHERE rolname IN ('pharma_owner', 'pharma_app');
