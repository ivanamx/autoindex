-- Mejora de búsqueda robusta:
-- 1) habilita extensiones para typo tolerance y normalización
-- 2) crea índices para acelerar full-text y trigram

CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Índice GIN para búsqueda full-text
CREATE INDEX IF NOT EXISTS idx_catalogos_texto_fts_unaccent
ON catalogos
USING gin (to_tsvector('simple', lower(texto)));

-- Índice GIN trigram para tolerancia a typos
CREATE INDEX IF NOT EXISTS idx_catalogos_texto_trgm_unaccent
ON catalogos
USING gin (lower(texto) gin_trgm_ops);

-- Índice por catálogo + página para filtrar y ordenar rápido
CREATE INDEX IF NOT EXISTS idx_catalogos_nombre_pagina
ON catalogos (catalogo_nombre, pagina);
