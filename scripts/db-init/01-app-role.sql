-- Crea el rol con el que se conecta la API.
--
-- Es deliberadamente distinto del propietario de las tablas. En PostgreSQL, el
-- propietario de una tabla evita sus propias políticas RLS salvo que se fuerce
-- `FORCE ROW LEVEL SECURITY`. Separar los roles hace que el aislamiento no
-- dependa de recordar ese detalle en cada migración.
--
-- `pharma_app`: NOSUPERUSER, NOBYPASSRLS, sin permiso para crear objetos.
-- Es lo único que garantiza que las políticas RLS se apliquen de verdad.

CREATE ROLE pharma_app LOGIN PASSWORD 'pharma_app_dev' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE pharma_sandbox TO pharma_app;
GRANT USAGE ON SCHEMA public TO pharma_app;

-- El rol de aplicación no puede crear tablas: solo leer y escribir en las que
-- creen las migraciones.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE pharma_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pharma_app;
ALTER DEFAULT PRIVILEGES FOR ROLE pharma_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO pharma_app;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
